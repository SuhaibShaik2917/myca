import json
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.gstr3b.schemas import (
    TaxBreakdown, Table31OutwardSupplies, Table4ITC,
    OffsetResult, GSTR3BStatusResponse, GSTR3BFilingResponse
)
from app.modules.gstr3b.offset_engine import compute_statutory_tax_offset
from app.modules.gstr3b.gsp_sandbox import simulate_gsp_gstn_filing, GSPSandboxFilingError
from app.modules.reconciliation.gstr3b_itc import generate_gstr3b_table4_summary
from app.database import set_tenant_context

GSTR3B_STATUTORY_DISCLAIMER = (
    "STATUTORY LEGAL NOTICE: This GSTR-3B return summary and tax offset computation is prepared "
    "by the MyCA statutory rules engine based on reconciled inward purchase records and outward supplies. "
    "In accordance with Rule 88A of the CGST Rules, 2017, credit offset must follow statutory priority. "
    "Submission to GSTN Sandbox is an idempotent simulation; live portal filing requires authorized GSP credentials."
)

class GSTR3BServiceError(Exception):
    pass

def prepare_gstr3b_return(
    session: Session,
    org_id: str,
    registration_id: str,
    tax_period: str,
    user_id: Optional[str] = None,
    snapshot_id: Optional[str] = None
) -> Dict[str, Any]:
    set_tenant_context(session, org_id=org_id, user_id=user_id)
    """
    Synthesizes:
    1. Table 3.1 Outward Taxable Supplies & Inward Reverse Charge Supplies.
    2. Table 4 Inward Reconciled Input Tax Credit (Available, Reversed, Net).
    3. Table 6.1 Electronic Credit & Cash Ledger Offset under Section 49, 49A, 49B & Rule 88A.
    Persists/updates GSTR-3B return record in public.gstr3b_filings with DRAFT status.
    """
    # 1. Fetch GSTIN
    reg_row = session.execute(
        text("SELECT gstin, legal_name FROM public.gst_registrations WHERE id = :reg AND org_id = :org;"),
        {"reg": registration_id, "org": org_id}
    ).fetchone()
    if not reg_row:
        raise GSTR3BServiceError(f"GST registration {registration_id} not found for this organization.")

    gstin = reg_row.gstin

    # Check if target return for tax period already exists (with row-level lock)
    existing_filing = session.execute(
        text("""
            SELECT id, filing_status, revision_number, table_3_1_data, table_4_data, offset_data, checker_id, checker_remarks, updated_at
            FROM public.gstr3b_filings 
            WHERE org_id = :org AND registration_id = :reg AND tax_period = :period
            FOR UPDATE;
        """),
        {"org": org_id, "reg": registration_id, "period": tax_period}
    ).fetchone()

    prev_snapshot_json = None
    target_revision = 1

    if existing_filing:
        status = existing_filing.filing_status
        if status == "FILED":
            session.rollback()
            raise GSTR3BServiceError(
                f"Statutory return for tax period '{tax_period}' has already been FILED and is strictly immutable. "
                "Corrections must be handled through separate statutory amendment workflows."
            )
        elif status == "SUBMITTED_FOR_REVIEW":
            session.rollback()
            raise GSTR3BServiceError(
                f"Cannot re-prepare return in 'SUBMITTED_FOR_REVIEW' status. "
                "It must be reviewed by the Checker or formally rejected before revisions can be made."
            )
        elif status == "APPROVED_BY_CHECKER":
            session.rollback()
            raise GSTR3BServiceError(
                f"Cannot re-prepare return in 'APPROVED_BY_CHECKER' status. "
                "Approved statutory snapshots are immutable. If changes are required, the return must be formally rejected or amended."
            )
        elif status == "REJECTED_BY_CHECKER":
            curr_rev = existing_filing.revision_number or 1
            target_revision = curr_rev + 1
            prev_snapshot_dict = {
                "revision": curr_rev,
                "table_3_1": existing_filing.table_3_1_data,
                "table_4": existing_filing.table_4_data,
                "offset": existing_filing.offset_data,
                "checker_id": str(existing_filing.checker_id) if existing_filing.checker_id else None,
                "checker_remarks": existing_filing.checker_remarks,
                "rejected_at": existing_filing.updated_at.isoformat() if existing_filing.updated_at else None
            }
            prev_snapshot_json = json.dumps(prev_snapshot_dict)
        elif status == "DRAFT":
            target_revision = existing_filing.revision_number or 1
            if getattr(existing_filing, "previous_snapshot", None):
                prev_snap = existing_filing.previous_snapshot
                prev_snapshot_json = json.dumps(prev_snap) if not isinstance(prev_snap, str) else prev_snap
        else:
            session.rollback()
            raise GSTR3BServiceError(
                f"Cannot re-prepare return in unrecognized status '{status}'."
            )

    # 2. Compute Table 3.1 Outward Taxable Supplies from approved invoices
    # Matches tax_period e.g. '2026-11'
    inv_rows = session.execute(
        text("""
        SELECT 
            COALESCE(SUM(total_taxable_amount), 0.00) as taxable,
            COALESCE(SUM(igst_amount), 0.00) as igst,
            COALESCE(SUM(cgst_amount), 0.00) as cgst,
            COALESCE(SUM(sgst_amount), 0.00) as sgst
        FROM public.invoices
        WHERE org_id = :org 
          AND status = 'approved'
          AND TO_CHAR(invoice_date, 'YYYY-MM') = :period;
        """),
        {"org": org_id, "period": tax_period}
    ).fetchone()

    taxable_outward = Decimal(str(inv_rows.taxable))
    igst_outward = Decimal(str(inv_rows.igst))
    cgst_outward = Decimal(str(inv_rows.cgst))
    sgst_outward = Decimal(str(inv_rows.sgst))
    total_tax_outward = igst_outward + cgst_outward + sgst_outward

    # Inward RCM supplies (attracting reverse charge where buyer is liable)
    rcm_rows = session.execute(
        text("""
        SELECT 
            COALESCE(SUM(total_taxable_amount), 0.00) as taxable,
            COALESCE(SUM(igst_amount), 0.00) as igst,
            COALESCE(SUM(cgst_amount), 0.00) as cgst,
            COALESCE(SUM(sgst_amount), 0.00) as sgst,
            COALESCE(SUM(cess_amount), 0.00) as cess
        FROM public.purchase_documents
        WHERE org_id = :org
          AND registration_id = :reg
          AND is_reverse_charge = true
          AND financial_period = :period;
        """),
        {"org": org_id, "reg": registration_id, "period": tax_period}
    ).fetchone()

    taxable_rcm = Decimal(str(rcm_rows.taxable))
    igst_rcm = Decimal(str(rcm_rows.igst))
    cgst_rcm = Decimal(str(rcm_rows.cgst))
    sgst_rcm = Decimal(str(rcm_rows.sgst))
    cess_rcm = Decimal(str(rcm_rows.cess))

    # Total Table 3.1 Tax Liability
    tot_igst_liab = igst_outward + igst_rcm
    tot_cgst_liab = cgst_outward + cgst_rcm
    tot_sgst_liab = sgst_outward + sgst_rcm
    tot_cess_liab = cess_rcm

    t31 = Table31OutwardSupplies(
        a_taxable_supplies=TaxBreakdown(
            taxable_value=taxable_outward,
            igst=igst_outward,
            cgst=cgst_outward,
            sgst=sgst_outward,
            cess=Decimal("0.00"),
            total=total_tax_outward
        ),
        d_inward_rcm=TaxBreakdown(
            taxable_value=taxable_rcm,
            igst=igst_rcm,
            cgst=cgst_rcm,
            sgst=sgst_rcm,
            cess=cess_rcm,
            total=igst_rcm + cgst_rcm + sgst_rcm + cess_rcm
        ),
        total_tax_payable=TaxBreakdown(
            taxable_value=taxable_outward + taxable_rcm,
            igst=tot_igst_liab,
            cgst=tot_cgst_liab,
            sgst=tot_sgst_liab,
            cess=tot_cess_liab,
            total=tot_igst_liab + tot_cgst_liab + tot_sgst_liab + tot_cess_liab
        )
    )

    # 3. Compute Table 4 Inward ITC from Purchase Register and ITC Ledger
    # Query approved, non-reversed purchase documents for this period
    pur_rows = session.execute(
        text("""
        SELECT 
            COALESCE(SUM(total_taxable_amount), 0.00) as taxable,
            COALESCE(SUM(igst_amount), 0.00) as igst,
            COALESCE(SUM(cgst_amount), 0.00) as cgst,
            COALESCE(SUM(sgst_amount), 0.00) as sgst,
            COALESCE(SUM(cess_amount), 0.00) as cess
        FROM public.purchase_documents
        WHERE org_id = :org
          AND registration_id = :reg
          AND financial_period = :period
          AND status = 'approved'
          AND receipt_status = 'RECEIVED';
        """),
        {"org": org_id, "reg": registration_id, "period": tax_period}
    ).fetchone()

    avail_igst = Decimal(str(pur_rows.igst))
    avail_cgst = Decimal(str(pur_rows.cgst))
    avail_sgst = Decimal(str(pur_rows.sgst))
    avail_cess = Decimal(str(pur_rows.cess))

    # Query reversals for this period from itc_ledger_entries
    rev_rows = session.execute(
        text("""
        SELECT 
            COALESCE(SUM(igst_amount), 0.00) as igst,
            COALESCE(SUM(cgst_amount), 0.00) as cgst,
            COALESCE(SUM(sgst_amount), 0.00) as sgst,
            COALESCE(SUM(cess_amount), 0.00) as cess
        FROM public.itc_ledger_entries
        WHERE org_id = :org
          AND tax_period = :period
          AND event_type LIKE 'REVERSAL_%';
        """),
        {"org": org_id, "period": tax_period}
    ).fetchone()

    rev_igst = Decimal(str(rev_rows.igst))
    rev_cgst = Decimal(str(rev_rows.cgst))
    rev_sgst = Decimal(str(rev_rows.sgst))
    rev_cess = Decimal(str(rev_rows.cess))

    net_igst = max(Decimal("0.00"), avail_igst - rev_igst)
    net_cgst = max(Decimal("0.00"), avail_cgst - rev_cgst)
    net_sgst = max(Decimal("0.00"), avail_sgst - rev_sgst)
    net_cess = max(Decimal("0.00"), avail_cess - rev_cess)

    t4 = Table4ITC(
        a_itc_available=TaxBreakdown(
            taxable_value=Decimal(str(pur_rows.taxable)),
            igst=avail_igst,
            cgst=avail_cgst,
            sgst=avail_sgst,
            cess=avail_cess,
            total=avail_igst + avail_cgst + avail_sgst + avail_cess
        ),
        b_itc_reversed=TaxBreakdown(
            igst=rev_igst,
            cgst=rev_cgst,
            sgst=rev_sgst,
            cess=rev_cess,
            total=rev_igst + rev_cgst + rev_sgst + rev_cess
        ),
        c_net_itc=TaxBreakdown(
            igst=net_igst,
            cgst=net_cgst,
            sgst=net_sgst,
            cess=net_cess,
            total=net_igst + net_cgst + net_sgst + net_cess
        )
    )

    # 4. Compute Legal Offset under Section 49, 49A, 49B & Rule 88A
    offset = compute_statutory_tax_offset(
        liability=t31.total_tax_payable,
        credit=t4.c_net_itc
    )

    # 5. Upsert into public.gstr3b_filings
    filing_id = str(uuid.uuid4())
    now_ts = datetime.now(timezone.utc)

    t31_dict = t31.model_dump(mode="json")
    t4_dict = t4.model_dump(mode="json")
    offset_dict = offset.model_dump(mode="json")

    upsert_stmt = text("""
        INSERT INTO public.gstr3b_filings (
            id, org_id, registration_id, tax_period, filing_status,
            table_3_1_data, table_4_data, offset_data, maker_id,
            maker_remarks, revision_number, previous_snapshot, updated_at
        ) VALUES (
            :id, :org_id, :reg_id, :period, 'DRAFT',
            CAST(:t31 AS jsonb), CAST(:t4 AS jsonb), CAST(:offset AS jsonb), :maker_id,
            'Prepared by MyCA rules engine from reconciled registers.', :rev, CAST(:prev_snap AS jsonb), :now
        )
        ON CONFLICT (org_id, registration_id, tax_period)
        DO UPDATE SET
            table_3_1_data = EXCLUDED.table_3_1_data,
            table_4_data = EXCLUDED.table_4_data,
            offset_data = EXCLUDED.offset_data,
            filing_status = 'DRAFT',
            maker_id = EXCLUDED.maker_id,
            maker_remarks = EXCLUDED.maker_remarks,
            revision_number = EXCLUDED.revision_number,
            previous_snapshot = EXCLUDED.previous_snapshot,
            checker_id = NULL,
            checker_remarks = NULL,
            updated_at = EXCLUDED.updated_at
        WHERE public.gstr3b_filings.filing_status IN ('DRAFT', 'REJECTED_BY_CHECKER')
        RETURNING id, filing_status, revision_number;
    """)

    res = session.execute(upsert_stmt, {
        "id": filing_id,
        "org_id": org_id,
        "reg_id": registration_id,
        "period": tax_period,
        "t31": json.dumps(t31_dict),
        "t4": json.dumps(t4_dict),
        "offset": json.dumps(offset_dict),
        "maker_id": user_id,
        "rev": target_revision,
        "prev_snap": prev_snapshot_json,
        "now": now_ts
    }).fetchone()

    if res is None:
        session.rollback()
        raise GSTR3BServiceError(
            f"Cannot re-prepare GSTR-3B return for tax period '{tax_period}': filing status prohibits re-preparation."
        )

    session.commit()

    return {
        "id": str(res.id),
        "registration_id": registration_id,
        "tax_period": tax_period,
        "filing_status": res.filing_status,
        "revision_number": res.revision_number,
        "table_3_1": t31_dict,
        "table_4": t4_dict,
        "offset": offset_dict,
        "disclaimer": GSTR3B_STATUTORY_DISCLAIMER
    }

def submit_gstr3b_for_review(
    session: Session,
    org_id: str,
    filing_id: str,
    user_id: str,
    remarks: Optional[str] = None
) -> Dict[str, Any]:
    """Maker submits prepared return for checker / CA review."""
    set_tenant_context(session, org_id=org_id, user_id=user_id)
    filing = session.execute(
        text("SELECT id, filing_status FROM public.gstr3b_filings WHERE id = :id AND org_id = :org FOR UPDATE;"),
        {"id": filing_id, "org": org_id}
    ).fetchone()
    if not filing:
        raise GSTR3BServiceError("GSTR-3B filing not found.")

    if filing.filing_status not in ("DRAFT", "REJECTED_BY_CHECKER"):
        session.rollback()
        raise GSTR3BServiceError(f"Cannot submit return in status '{filing.filing_status}'. Must be DRAFT or REJECTED_BY_CHECKER.")

    session.execute(
        text("""
        UPDATE public.gstr3b_filings
        SET filing_status = 'SUBMITTED_FOR_REVIEW',
            maker_id = :uid,
            maker_remarks = :rem,
            updated_at = clock_timestamp()
        WHERE id = :id AND org_id = :org;
        """),
        {"id": filing_id, "org": org_id, "uid": user_id, "rem": remarks or "Submitted for CA review."}
    )
    session.commit()
    return {"id": filing_id, "status": "SUBMITTED_FOR_REVIEW", "message": "Return successfully submitted for Checker / CA review."}

def review_gstr3b_decision(
    session: Session,
    org_id: str,
    filing_id: str,
    checker_id: str,
    decision: str,
    remarks: str
) -> Dict[str, Any]:
    """Checker / CA approves or rejects return."""
    set_tenant_context(session, org_id=org_id, user_id=checker_id)
    filing = session.execute(
        text("SELECT id, filing_status, maker_id FROM public.gstr3b_filings WHERE id = :id AND org_id = :org FOR UPDATE;"),
        {"id": filing_id, "org": org_id}
    ).fetchone()
    if not filing:
        raise GSTR3BServiceError("GSTR-3B filing not found.")

    if filing.filing_status != "SUBMITTED_FOR_REVIEW":
        session.rollback()
        raise GSTR3BServiceError(f"Cannot review return in status '{filing.filing_status}'. Must be SUBMITTED_FOR_REVIEW.")

    # Strict separation of duties: Maker cannot act as Checker across all environments
    if not checker_id:
        session.rollback()
        raise GSTR3BServiceError("Checker user identity required for review.")
    if filing.maker_id and str(filing.maker_id) == str(checker_id):
        session.rollback()
        raise GSTR3BServiceError(
            "Segregation of duties violation: Maker cannot act as Checker to approve or reject their own GSTR-3B submission."
        )

    # Authoritative reviewer membership & role validation in public.organization_memberships
    membership_row = session.execute(
        text("""
        SELECT role::text as role, status::text as status
        FROM public.organization_memberships
        WHERE org_id = :org AND user_id = :uid;
        """),
        {"org": org_id, "uid": checker_id}
    ).fetchone()

    if not membership_row:
        session.rollback()
        raise GSTR3BServiceError(
            "Unauthorized reviewer: Checker is not a member of the organization."
        )

    if membership_row.status != "active":
        session.rollback()
        raise GSTR3BServiceError(
            f"Unauthorized reviewer: Checker membership status is '{membership_row.status}'. Must be 'active'."
        )

    if membership_row.role not in ("admin", "owner"):
        session.rollback()
        raise GSTR3BServiceError(
            f"Unauthorized reviewer: Checker possesses role '{membership_row.role}'. "
            "Statutory review of GSTR-3B returns requires an authoritative 'admin' or 'owner' role."
        )

    new_status = "APPROVED_BY_CHECKER" if decision == "APPROVED" else "REJECTED_BY_CHECKER"
    session.execute(
        text("""
        UPDATE public.gstr3b_filings
        SET filing_status = :st,
            checker_id = :cid,
            checker_remarks = :rem,
            updated_at = clock_timestamp()
        WHERE id = :id AND org_id = :org;
        """),
        {"id": filing_id, "org": org_id, "st": new_status, "cid": checker_id, "rem": remarks}
    )
    session.commit()
    return {"id": filing_id, "status": new_status, "decision": decision, "remarks": remarks}

def file_gstr3b_to_gsp_sandbox(
    session: Session,
    org_id: str,
    filing_id: str,
    user_id: str
) -> GSTR3BFilingResponse:
    """Executes filing to GSP sandbox after checker approval."""
    set_tenant_context(session, org_id=org_id, user_id=user_id)
    filing = session.execute(
        text("""
        SELECT f.*, r.gstin
        FROM public.gstr3b_filings f
        JOIN public.gst_registrations r ON f.registration_id = r.id AND f.org_id = r.org_id
        WHERE f.id = :id AND f.org_id = :org
        FOR UPDATE OF f;
        """),
        {"id": filing_id, "org": org_id}
    ).fetchone()
    if not filing:
        raise GSTR3BServiceError("GSTR-3B filing not found.")

    # Gate: must be approved by checker
    if filing.filing_status != "APPROVED_BY_CHECKER":
        session.rollback()
        raise GSTR3BServiceError(
            f"Filing Gate Blocked: Cannot file return in status '{filing.filing_status}'. "
            "GSTR-3B must be APPROVED_BY_CHECKER prior to submission."
        )

    payload_dict = {
        "org_id": org_id,
        "registration_id": str(filing.registration_id),
        "gstin": filing.gstin,
        "tax_period": filing.tax_period,
        "table_3_1": filing.table_3_1_data,
        "table_4": filing.table_4_data,
        "offset": filing.offset_data
    }

    try:
        arn, cin, filing_date_iso, payload_hash = simulate_gsp_gstn_filing(
            gstin=filing.gstin,
            tax_period=filing.tax_period,
            payload_dict=payload_dict,
            checker_approved=True
        )
    except GSPSandboxFilingError as e:
        raise GSTR3BServiceError(str(e))

    # Update filing record
    session.execute(
        text("""
        UPDATE public.gstr3b_filings
        SET filing_status = 'FILED',
            arn = :arn,
            cin = :cin,
            filing_date = :fdate,
            payload_hash = :phash,
            updated_at = clock_timestamp()
        WHERE id = :id AND org_id = :org_id;
        """),
        {
            "id": filing_id,
            "org_id": org_id,
            "arn": arn,
            "cin": cin,
            "fdate": filing_date_iso,
            "phash": payload_hash
        }
    )

    # Upsert public.gst_records for statutory compliance tracking
    total_taxable = Decimal(str(filing.table_3_1_data.get("total_tax_payable", {}).get("taxable_value", 0)))
    total_paid = Decimal(str(filing.offset_data.get("total_tax_paid_by_cash", 0))) + Decimal(str(filing.offset_data.get("total_tax_paid_by_credit", 0)))
    session.execute(
        text("""
        INSERT INTO public.gst_records (
            id, org_id, gstin, return_type, tax_period, filing_status,
            filing_date, total_taxable_value, total_tax_paid
        ) VALUES (
            gen_random_uuid(), :org, :gstin, 'GSTR-3B', :period, 'filed',
            CURRENT_DATE, :taxable, :paid
        )
        ON CONFLICT DO NOTHING;
        """),
        {
            "org": org_id,
            "gstin": filing.gstin,
            "period": filing.tax_period,
            "taxable": total_taxable,
            "paid": total_paid
        }
    )

    session.commit()

    return GSTR3BFilingResponse(
        filing_id=filing_id,
        tax_period=filing.tax_period,
        status="FILED",
        arn=arn,
        cin=cin,
        filing_date=filing_date_iso,
        payload_hash=payload_hash,
        table_3_1=Table31OutwardSupplies.model_validate(filing.table_3_1_data),
        table_4=Table4ITC.model_validate(filing.table_4_data),
        offset=OffsetResult.model_validate(filing.offset_data),
        maker_remarks=filing.maker_remarks,
        checker_remarks=filing.checker_remarks,
        is_live_portal_filing=False,
        disclaimer=GSTR3B_STATUTORY_DISCLAIMER
    )

def get_gstr3b_status(
    session: Session,
    org_id: str,
    registration_id: str,
    tax_period: str
) -> Optional[Dict[str, Any]]:
    """Retrieves current return state for period."""
    set_tenant_context(session, org_id=org_id, user_id=None)
    row = session.execute(
        text("""
        SELECT * FROM public.gstr3b_filings
        WHERE org_id = :org AND registration_id = :reg AND tax_period = :period;
        """),
        {"org": org_id, "reg": registration_id, "period": tax_period}
    ).fetchone()
    if not row:
        return None

    return {
        "id": str(row.id),
        "registration_id": str(row.registration_id),
        "tax_period": row.tax_period,
        "filing_status": row.filing_status,
        "revision_number": getattr(row, "revision_number", 1) or 1,
        "previous_snapshot": getattr(row, "previous_snapshot", None),
        "table_3_1": row.table_3_1_data,
        "table_4": row.table_4_data,
        "offset": row.offset_data,
        "maker_id": str(row.maker_id) if row.maker_id else None,
        "checker_id": str(row.checker_id) if row.checker_id else None,
        "maker_remarks": row.maker_remarks,
        "checker_remarks": row.checker_remarks,
        "arn": row.arn,
        "cin": row.cin,
        "filing_date": row.filing_date.isoformat() if row.filing_date else None,
        "payload_hash": row.payload_hash,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "disclaimer": GSTR3B_STATUTORY_DISCLAIMER
    }
