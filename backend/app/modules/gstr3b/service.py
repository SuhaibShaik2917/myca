import hashlib
import hmac
import json
import secrets
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings

from app.modules.gstr3b.schemas import (
    TaxBreakdown, Table31OutwardSupplies, Table4ITC,
    OffsetResult, GSTR3BStatusResponse, GSTR3BFilingResponse
)
from app.modules.gstr3b.offset_engine import compute_statutory_tax_offset
from app.modules.gstr3b.gsp_sandbox import GSPSandboxFilingError
from app.modules.gstr3b.gsp_client import dispatch_gstr3b_filing, GSPIntegrationUnavailableError
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


def _get_gstr3b_attestation_key() -> bytes:
    """
    Returns derived HMAC key for GSTR-3B database attestations.
    In production and staging, requires an explicit strong GSTR3B_REVIEW_ATTESTATION_KEY
    distinct from SECRET_KEY (fails closed if missing).
    """
    env_norm = (settings.ENVIRONMENT or "").strip().lower()
    if env_norm in ("production", "staging"):
        key = settings.GSTR3B_REVIEW_ATTESTATION_KEY
        if not key or not key.strip() or len(key) < 32:
            raise GSTR3BServiceError(
                "CRITICAL SECURITY ERROR: GSTR3B_REVIEW_ATTESTATION_KEY (>= 32 chars) is required in production and staging."
            )
        if key == settings.SECRET_KEY:
            raise GSTR3BServiceError(
                "CRITICAL SECURITY ERROR: GSTR3B_REVIEW_ATTESTATION_KEY must be distinct from SECRET_KEY in production and staging."
            )
        raw_key = key
    else:
        raw_key = settings.GSTR3B_REVIEW_ATTESTATION_KEY or f"MYCA_GSTR3B_REVIEW_TRUST_V1:{settings.SECRET_KEY}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest().encode("utf-8")


def verify_gstr3b_trust_anchor_sync(session: Session) -> bool:
    """
    Verifies that the configured GSTR3B attestation key matches the database
    trust anchor without modifying or rotating the stored trust anchor.
    """
    expected_digest = _get_gstr3b_attestation_key().decode("utf-8")
    matched = session.execute(
        text("SELECT public.verify_gstr3b_trust_anchor_match(:expected);"),
        {"expected": expected_digest},
    ).scalar()
    return bool(matched)


def _sign_gstr3b_review_attestation(
    org_id: str,
    filing_id: str,
    checker_id: str,
    new_status: str,
    nonce: str,
) -> str:
    """Computes server-side HMAC-SHA256 attestation for Checker review transition."""
    derived_secret = _get_gstr3b_attestation_key()
    message = f"{org_id}:{filing_id}:{checker_id}:{new_status}:{nonce}".encode("utf-8")
    return hmac.new(derived_secret, message, hashlib.sha256).hexdigest()


def _compute_gstr3b_payload_digest(t31_text: str, t4_text: str, offset_text: str) -> str:
    return hashlib.sha256(f"{t31_text}:{t4_text}:{offset_text}".encode("utf-8")).hexdigest()


def _compute_gstr3b_meta_digest(rev: int, maker_remarks: Optional[str], prev_snap_text: Optional[str]) -> str:
    return hashlib.sha256(
        f"{rev}:{maker_remarks or ''}:{prev_snap_text or ''}".encode("utf-8")
    ).hexdigest()


def _sign_gstr3b_creation_attestation(
    org_id: str,
    filing_id: str,
    reg_id: str,
    tax_period: str,
    maker_id: str,
    payload_digest: str,
    meta_digest: str,
    nonce: str,
) -> str:
    """Computes server-side HMAC-SHA256 attestation for GSTR-3B DRAFT creation/preparation."""
    derived_secret = _get_gstr3b_attestation_key()
    message = (
        f"{org_id}:{filing_id}:{reg_id}:{tax_period}:{maker_id}:DRAFT:{payload_digest}:{meta_digest}:{nonce}"
    ).encode("utf-8")
    return hmac.new(derived_secret, message, hashlib.sha256).hexdigest()


def _sign_gstr3b_legacy_maker_claim(
    org_id: str,
    filing_id: str,
    maker_id: str,
    reason: str,
    nonce: str,
) -> str:
    """Computes server-side HMAC-SHA256 attestation for one-time legacy NULL maker_id claim."""
    derived_secret = _get_gstr3b_attestation_key()
    message = f"{org_id}:{filing_id}:{maker_id}:LEGACY_MAKER_CLAIM:{reason}:{nonce}".encode("utf-8")
    return hmac.new(derived_secret, message, hashlib.sha256).hexdigest()


def _sign_gstr3b_filing_attestation(
    org_id: str,
    filing_id: str,
    actor_id: str,
    arn: str,
    cin: Optional[str],
    payload_hash: str,
    nonce: str,
) -> str:
    """Computes server-side HMAC-SHA256 attestation for APPROVED_BY_CHECKER -> FILED transition."""
    derived_secret = _get_gstr3b_attestation_key()
    message = f"{org_id}:{filing_id}:{actor_id}:FILED:{arn}:{cin or ''}:{payload_hash}:{nonce}".encode("utf-8")
    return hmac.new(derived_secret, message, hashlib.sha256).hexdigest()


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
            SELECT id, filing_status, revision_number, table_3_1_data, table_4_data, offset_data,
                   maker_id, maker_remarks, checker_id, checker_remarks, previous_snapshot, updated_at
            FROM public.gstr3b_filings 
            WHERE org_id = :org AND registration_id = :reg AND tax_period = :period
            FOR UPDATE;
        """),
        {"org": org_id, "reg": registration_id, "period": tax_period}
    ).fetchone()

    prev_snapshot_json = None
    rejected_archive_row = None
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
                "maker_id": str(existing_filing.maker_id) if existing_filing.maker_id else None,
                "checker_id": str(existing_filing.checker_id) if existing_filing.checker_id else None,
                "maker_remarks": existing_filing.maker_remarks,
                "checker_remarks": existing_filing.checker_remarks,
                "rejected_at": existing_filing.updated_at.isoformat() if existing_filing.updated_at else None
            }
            prev_snapshot_json = json.dumps(prev_snapshot_dict)
            rejected_archive_row = {
                "filing_id": str(existing_filing.id),
                "org_id": org_id,
                "rev_num": curr_rev,
                "t31": json.dumps(existing_filing.table_3_1_data),
                "t4": json.dumps(existing_filing.table_4_data),
                "offset": json.dumps(existing_filing.offset_data),
                "maker_id": str(existing_filing.maker_id) if existing_filing.maker_id else None,
                "checker_id": str(existing_filing.checker_id) if existing_filing.checker_id else None,
                "maker_remarks": existing_filing.maker_remarks,
                "checker_remarks": existing_filing.checker_remarks,
                "rejected_at": existing_filing.updated_at,
            }
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

    # 2. Compute Table 3.1 Outward Taxable Supplies from approved invoices scoped to registration GSTIN
    # Matches tax_period e.g. '2026-11', segregates supply categories, and subtracts credit notes.
    inv_rows = session.execute(
        text("""
        SELECT
            i.supply_category,
            i.is_sez_supply,
            i.is_reverse_charge,
            i.document_type,
            COALESCE(i.total_taxable_amount, 0.00) as taxable,
            COALESCE(i.igst_amount, 0.00) as igst,
            COALESCE(i.cgst_amount, 0.00) as cgst,
            COALESCE(i.sgst_amount, 0.00) as sgst
        FROM public.invoices i
        LEFT JOIN public.business_profiles p ON i.profile_id = p.id AND i.org_id = p.org_id
        WHERE i.org_id = :org
          AND i.status = 'approved'
          AND TO_CHAR(i.invoice_date, 'YYYY-MM') = :period
          AND (
              UPPER(TRIM(p.gstin)) = UPPER(TRIM(:gstin))
              OR (
                  p.gstin IS NULL
                  AND (
                      CAST(i.profile_id AS text) = CAST(:reg AS text)
                      OR NOT EXISTS (
                          SELECT 1 FROM public.gst_registrations g2
                          WHERE g2.org_id = :org AND g2.id <> CAST(:reg AS uuid)
                      )
                  )
              )
          );
        """),
        {"org": org_id, "period": tax_period, "gstin": gstin, "reg": registration_id}
    ).fetchall()

    taxable_outward = Decimal("0.00")
    igst_outward = Decimal("0.00")
    cgst_outward = Decimal("0.00")
    sgst_outward = Decimal("0.00")
    b_taxable = Decimal("0.00")
    b_igst = Decimal("0.00")
    c_nil_exempt = Decimal("0.00")

    for row in inv_rows:
        sup_cat = str(row.supply_category or "").strip().upper()
        is_sez = bool(row.is_sez_supply)
        is_rcm = bool(row.is_reverse_charge)
        doc_type = str(row.document_type or "").strip().lower()
        sign = Decimal("-1.00") if doc_type in ("credit_note", "revised_credit_note") else Decimal("1.00")
        tx = sign * Decimal(str(row.taxable or 0))
        ig = sign * Decimal(str(row.igst or 0))
        cg = sign * Decimal(str(row.cgst or 0))
        sg = sign * Decimal(str(row.sgst or 0))
        if sup_cat == "NIL_EXEMPT":
            c_nil_exempt += tx
        elif is_sez or sup_cat in ("SEZ_WITH_PAYMENT", "SEZ_WITHOUT_PAYMENT", "ZERO_RATED", "EXPORT_WITH_PAYMENT", "EXPORT_WITHOUT_PAYMENT"):
            b_taxable += tx
            b_igst += ig
        elif not is_rcm and sup_cat != "B2B_REVERSE_CHARGE":
            taxable_outward += tx
            igst_outward += ig
            cgst_outward += cg
            sgst_outward += sg

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
    tot_igst_liab = igst_outward + b_igst + igst_rcm
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
        b_zero_rated=TaxBreakdown(
            taxable_value=b_taxable,
            igst=b_igst,
            cgst=Decimal("0.00"),
            sgst=Decimal("0.00"),
            cess=Decimal("0.00"),
            total=b_igst
        ),
        c_other_nil_exempt=c_nil_exempt,
        d_inward_rcm=TaxBreakdown(
            taxable_value=taxable_rcm,
            igst=igst_rcm,
            cgst=cgst_rcm,
            sgst=sgst_rcm,
            cess=cess_rcm,
            total=igst_rcm + cgst_rcm + sgst_rcm + cess_rcm
        ),
        total_tax_payable=TaxBreakdown(
            taxable_value=taxable_outward + b_taxable + taxable_rcm,
            igst=tot_igst_liab,
            cgst=tot_cgst_liab,
            sgst=tot_sgst_liab,
            cess=tot_cess_liab,
            total=tot_igst_liab + tot_cgst_liab + tot_sgst_liab + tot_cess_liab
        )
    )

    # 3. Compute Table 4 Inward ITC from reconciled GSTR-2B snapshot (if supplied) or Purchase Register & ITC Ledger
    if snapshot_id:
        snap_exists = session.execute(
            text("""
            SELECT id FROM public.gstr2b_snapshots
            WHERE id = :sid AND org_id = :org AND registration_id = :reg;
            """),
            {"sid": snapshot_id, "org": org_id, "reg": registration_id},
        ).fetchone()
        if not snap_exists:
            session.rollback()
            raise GSTR3BServiceError(f"GSTR-2B snapshot {snapshot_id} not found for this registration.")

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
          AND receipt_status IN ('RECEIVED', 'DEEMED_RECEIVED');
        """),
        {"org": org_id, "reg": registration_id, "period": tax_period}
    ).fetchone()

    avail_igst = Decimal(str(pur_rows.igst))
    avail_cgst = Decimal(str(pur_rows.cgst))
    avail_sgst = Decimal(str(pur_rows.sgst))
    avail_cess = Decimal(str(pur_rows.cess))

    # Query reversals for this registration & period from itc_ledger_entries
    rev_rows = session.execute(
        text("""
        SELECT 
            COALESCE(SUM(e.igst_amount), 0.00) as igst,
            COALESCE(SUM(e.cgst_amount), 0.00) as cgst,
            COALESCE(SUM(e.sgst_amount), 0.00) as sgst,
            COALESCE(SUM(e.cess_amount), 0.00) as cess
        FROM public.itc_ledger_entries e
        JOIN public.purchase_documents p ON e.purchase_doc_id = p.id AND e.org_id = p.org_id
        WHERE e.org_id = :org
          AND p.registration_id = :reg
          AND e.tax_period = :period
          AND e.event_type LIKE 'REVERSAL_%';
        """),
        {"org": org_id, "reg": registration_id, "period": tax_period}
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
    # Statutory Invariant (Sec 49(4)): Table 3.1(d) Inward RCM CANNOT be offset via ITC; must be paid in CASH (PMT-06).
    outward_taxable_liability = TaxBreakdown(
        taxable_value=t31.a_taxable_supplies.taxable_value + t31.b_zero_rated.taxable_value,
        igst=t31.a_taxable_supplies.igst + t31.b_zero_rated.igst,
        cgst=t31.a_taxable_supplies.cgst,
        sgst=t31.a_taxable_supplies.sgst,
        cess=Decimal("0.00"),
        total=t31.a_taxable_supplies.total + t31.b_zero_rated.total,
    )
    offset = compute_statutory_tax_offset(
        liability=outward_taxable_liability,
        credit=t4.c_net_itc
    )
    if (igst_rcm + cgst_rcm + sgst_rcm + cess_rcm) > Decimal("0.00"):
        offset.igst.liability_before = (offset.igst.liability_before + igst_rcm).quantize(Decimal("0.01"))
        offset.igst.remaining_liability = (offset.igst.remaining_liability + igst_rcm).quantize(Decimal("0.01"))
        offset.igst.challan_required = (offset.igst.challan_required + igst_rcm).quantize(Decimal("0.01"))
        offset.cgst.liability_before = (offset.cgst.liability_before + cgst_rcm).quantize(Decimal("0.01"))
        offset.cgst.remaining_liability = (offset.cgst.remaining_liability + cgst_rcm).quantize(Decimal("0.01"))
        offset.cgst.challan_required = (offset.cgst.challan_required + cgst_rcm).quantize(Decimal("0.01"))
        offset.sgst.liability_before = (offset.sgst.liability_before + sgst_rcm).quantize(Decimal("0.01"))
        offset.sgst.remaining_liability = (offset.sgst.remaining_liability + sgst_rcm).quantize(Decimal("0.01"))
        offset.sgst.challan_required = (offset.sgst.challan_required + sgst_rcm).quantize(Decimal("0.01"))
        offset.cess.liability_before = (offset.cess.liability_before + cess_rcm).quantize(Decimal("0.01"))
        offset.cess.remaining_liability = (offset.cess.remaining_liability + cess_rcm).quantize(Decimal("0.01"))
        offset.cess.challan_required = (offset.cess.challan_required + cess_rcm).quantize(Decimal("0.01"))
        rcm_tot = (igst_rcm + cgst_rcm + sgst_rcm + cess_rcm).quantize(Decimal("0.01"))
        offset.total_challan_required = (offset.total_challan_required + rcm_tot).quantize(Decimal("0.01"))
        offset.statutory_ordering_audit.append(
            f"Section 49(4) RCM Gate: Table 3.1(d) inward reverse charge tax ₹{rcm_tot:,.2f} excluded from ITC credit offset and routed strictly to Electronic Cash Ledger (PMT-06)."
        )

    # 5. Upsert into public.gstr3b_filings via restricted SECURITY DEFINER creation function
    filing_id = str(existing_filing.id) if existing_filing else str(uuid.uuid4())
    effective_maker_id = (
        str(existing_filing.maker_id)
        if (existing_filing and existing_filing.maker_id)
        else user_id
    )
    if not effective_maker_id:
        session.rollback()
        raise GSTR3BServiceError("Authenticated maker_id is required to prepare a GSTR-3B return.")

    # If re-preparing a legacy filing whose maker_id is NULL, execute attested one-time claim first
    if existing_filing and existing_filing.maker_id is None:
        claim_reason = "Claimed during re-preparation of legacy filing with unknown original maker."
        claim_nonce = secrets.token_hex(24)
        claim_sig = _sign_gstr3b_legacy_maker_claim(
            str(org_id), filing_id, str(effective_maker_id), claim_reason, claim_nonce
        )
        session.execute(
            text("""
            SELECT public.claim_legacy_null_maker(
                CAST(:org AS uuid),
                CAST(:id AS uuid),
                CAST(:uid AS uuid),
                :reason,
                :nonce,
                :sig
            );
            """),
            {
                "org": str(org_id),
                "id": filing_id,
                "uid": str(effective_maker_id),
                "reason": claim_reason,
                "nonce": claim_nonce,
                "sig": claim_sig,
            }
        )

    t31_dict = t31.model_dump(mode="json")
    t4_dict = t4.model_dump(mode="json")
    offset_dict = offset.model_dump(mode="json")

    t31_text = json.dumps(t31_dict, sort_keys=True, separators=(",", ":"))
    t4_text = json.dumps(t4_dict, sort_keys=True, separators=(",", ":"))
    offset_text = json.dumps(offset_dict, sort_keys=True, separators=(",", ":"))
    maker_remarks_val = "Prepared by MyCA rules engine from reconciled registers."
    payload_digest = _compute_gstr3b_payload_digest(t31_text, t4_text, offset_text)
    meta_digest = _compute_gstr3b_meta_digest(target_revision, maker_remarks_val, prev_snapshot_json)
    nonce = secrets.token_hex(24)
    signature = _sign_gstr3b_creation_attestation(
        org_id=str(org_id),
        filing_id=filing_id,
        reg_id=str(registration_id),
        tax_period=tax_period,
        maker_id=str(effective_maker_id),
        payload_digest=payload_digest,
        meta_digest=meta_digest,
        nonce=nonce,
    )

    upsert_stmt = text("""
        SELECT id, filing_status, revision_number
        FROM public.execute_gstr3b_prepare_upsert(
            CAST(:id AS uuid),
            CAST(:org_id AS uuid),
            CAST(:reg_id AS uuid),
            :period,
            :t31_text,
            :t4_text,
            :offset_text,
            CAST(:maker_id AS uuid),
            :maker_remarks,
            :rev,
            :prev_snap_text,
            :digest,
            :meta_digest,
            :nonce,
            :sig
        );
    """)

    res = session.execute(upsert_stmt, {
        "id": filing_id,
        "org_id": str(org_id),
        "reg_id": str(registration_id),
        "period": tax_period,
        "t31_text": t31_text,
        "t4_text": t4_text,
        "offset_text": offset_text,
        "maker_id": str(effective_maker_id),
        "maker_remarks": maker_remarks_val,
        "rev": target_revision,
        "prev_snap_text": prev_snapshot_json,
        "digest": payload_digest,
        "meta_digest": meta_digest,
        "nonce": nonce,
        "sig": signature,
    }).fetchone()

    if res is None:
        session.rollback()
        raise GSTR3BServiceError(
            f"Cannot re-prepare GSTR-3B return for tax period '{tax_period}': filing status prohibits re-preparation."
        )

    if rejected_archive_row is not None:
        session.execute(
            text("""
            INSERT INTO public.gstr3b_revision_history (
                filing_id, org_id, revision_number,
                table_3_1_data, table_4_data, offset_data,
                maker_id, checker_id, maker_remarks, checker_remarks, rejected_at, archived_at
            ) VALUES (
                :filing_id, :org_id, :rev_num,
                CAST(:t31 AS jsonb), CAST(:t4 AS jsonb), CAST(:offset AS jsonb),
                :maker_id, :checker_id, :maker_remarks, :checker_remarks, :rejected_at, clock_timestamp()
            )
            ON CONFLICT (filing_id, revision_number) DO NOTHING;
            """),
            rejected_archive_row
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
        text("SELECT id, filing_status, maker_id FROM public.gstr3b_filings WHERE id = :id AND org_id = :org FOR UPDATE;"),
        {"id": filing_id, "org": org_id}
    ).fetchone()
    if not filing:
        raise GSTR3BServiceError("GSTR-3B filing not found.")

    if filing.filing_status not in ("DRAFT", "REJECTED_BY_CHECKER"):
        session.rollback()
        raise GSTR3BServiceError(f"Cannot submit return in status '{filing.filing_status}'. Must be DRAFT or REJECTED_BY_CHECKER.")

    if filing.maker_id is None:
        if not user_id:
            session.rollback()
            raise GSTR3BServiceError("Authenticated maker identity required to claim legacy GSTR-3B filing.")
        claim_reason = "Claimed during submission of legacy filing with unknown original maker."
        claim_nonce = secrets.token_hex(24)
        claim_sig = _sign_gstr3b_legacy_maker_claim(str(org_id), str(filing_id), str(user_id), claim_reason, claim_nonce)
        session.execute(
            text("""
            SELECT public.claim_legacy_null_maker(
                CAST(:org AS uuid),
                CAST(:id AS uuid),
                CAST(:uid AS uuid),
                :reason,
                :nonce,
                :sig
            );
            """),
            {
                "org": str(org_id),
                "id": str(filing_id),
                "uid": str(user_id),
                "reason": claim_reason,
                "nonce": claim_nonce,
                "sig": claim_sig,
            }
        )

    session.execute(
        text("""
        UPDATE public.gstr3b_filings
        SET filing_status = 'SUBMITTED_FOR_REVIEW',
            maker_remarks = :rem,
            updated_at = clock_timestamp()
        WHERE id = :id AND org_id = :org;
        """),
        {"id": filing_id, "org": org_id, "rem": remarks or "Submitted for CA review."}
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
    nonce = secrets.token_hex(24)
    signature = _sign_gstr3b_review_attestation(
        org_id=str(org_id),
        filing_id=str(filing_id),
        checker_id=str(checker_id),
        new_status=new_status,
        nonce=nonce,
    )
    session.execute(
        text("""
        SELECT public.execute_gstr3b_review_transition(
            CAST(:org AS uuid),
            CAST(:id AS uuid),
            CAST(:cid AS uuid),
            :st,
            :rem,
            :nonce,
            :sig
        );
        """),
        {
            "org": str(org_id),
            "id": str(filing_id),
            "cid": str(checker_id),
            "st": new_status,
            "rem": remarks,
            "nonce": nonce,
            "sig": signature,
        }
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
        arn, cin, filing_date_iso, payload_hash, is_live_portal_filing = dispatch_gstr3b_filing(
            gstin=filing.gstin,
            tax_period=filing.tax_period,
            payload_dict=payload_dict,
            checker_approved=True
        )
    except GSPSandboxFilingError as e:
        raise GSTR3BServiceError(str(e))

    filing_mode = "LIVE_PORTAL" if is_live_portal_filing else "SANDBOX"
    gsp_ref_id = arn if is_live_portal_filing else f"SANDBOX-{arn}"

    # Update filing record through restricted SECURITY DEFINER filing transition v2 (migration 0040)
    nonce = secrets.token_hex(24)
    signature = _sign_gstr3b_filing_attestation(
        org_id=str(org_id),
        filing_id=str(filing_id),
        actor_id=str(user_id),
        arn=arn,
        cin=cin,
        payload_hash=payload_hash,
        nonce=nonce,
    )
    session.execute(
        text("""
        SELECT public.execute_gstr3b_filing_transition_v2(
            CAST(:org_id AS uuid),
            CAST(:id AS uuid),
            CAST(:actor_id AS uuid),
            :arn,
            :cin,
            CAST(:fdate AS timestamptz),
            :phash,
            :nonce,
            :sig,
            :fmode,
            :gsp_ref
        );
        """),
        {
            "org_id": str(org_id),
            "id": str(filing_id),
            "actor_id": str(user_id),
            "arn": arn,
            "cin": cin,
            "fdate": filing_date_iso,
            "phash": payload_hash,
            "nonce": nonce,
            "sig": signature,
            "fmode": filing_mode,
            "gsp_ref": gsp_ref_id,
        }
    )

    # Upsert public.gst_records with explicit filing_mode & lineage
    total_taxable = Decimal(str(filing.table_3_1_data.get("total_tax_payable", {}).get("taxable_value", 0)))
    total_paid = (
        Decimal(str(filing.offset_data.get("total_tax_paid_by_cash", 0)))
        + Decimal(str(filing.offset_data.get("total_tax_paid_by_credit", 0)))
        + Decimal(str(filing.offset_data.get("total_challan_required", 0)))
    )
    session.execute(
        text("""
        INSERT INTO public.gst_records (
            id, org_id, registration_id, gstr3b_filing_id, gstin, return_type, tax_period,
            filing_status, filing_date, total_taxable_value, total_tax_paid,
            filing_mode, is_live_portal_filing, gsp_reference_id, arn
        ) VALUES (
            gen_random_uuid(), :org, CAST(:reg_id AS uuid), CAST(:fid AS uuid), :gstin, 'GSTR-3B', :period,
            'filed', CURRENT_DATE, :taxable, :paid,
            :fmode, :is_live, :gsp_ref, :arn
        )
        ON CONFLICT (org_id, gstin, return_type, tax_period, filing_mode)
        DO UPDATE SET
            gstr3b_filing_id = EXCLUDED.gstr3b_filing_id,
            filing_status = EXCLUDED.filing_status,
            filing_date = EXCLUDED.filing_date,
            total_taxable_value = EXCLUDED.total_taxable_value,
            total_tax_paid = EXCLUDED.total_tax_paid,
            is_live_portal_filing = EXCLUDED.is_live_portal_filing,
            gsp_reference_id = EXCLUDED.gsp_reference_id,
            arn = EXCLUDED.arn;
        """),
        {
            "org": org_id,
            "reg_id": str(filing.registration_id),
            "fid": str(filing_id),
            "gstin": filing.gstin,
            "period": filing.tax_period,
            "taxable": total_taxable,
            "paid": total_paid,
            "fmode": filing_mode,
            "is_live": is_live_portal_filing,
            "gsp_ref": gsp_ref_id,
            "arn": arn,
        }
    )

    # Atomically freeze IMS actions and active GSTR-2B snapshot for this registration + tax period
    session.execute(
        text("""
        UPDATE public.ims_document_actions a
        SET is_frozen_by_gstr3b = true,
            gstr3b_filing_date = CAST(:fdate AS timestamptz),
            updated_at = clock_timestamp()
        FROM public.gstr2b_source_documents d
        JOIN public.gstr2b_snapshots s ON d.snapshot_id = s.id AND d.org_id = s.org_id
        WHERE a.gstr2b_doc_id = d.id AND a.org_id = :org_id
          AND s.registration_id = CAST(:reg_id AS uuid) AND s.return_period = :period;
        """),
        {"org_id": str(org_id), "reg_id": str(filing.registration_id), "period": filing.tax_period, "fdate": filing_date_iso},
    )
    session.execute(
        text("""
        UPDATE public.gstr2b_snapshots
        SET is_frozen = true,
            filing_confirmation_arn = :arn,
            filed_at = CAST(:fdate AS timestamptz)
        WHERE org_id = :org_id AND registration_id = CAST(:reg_id AS uuid) AND return_period = :period AND is_active = true;
        """),
        {"org_id": str(org_id), "reg_id": str(filing.registration_id), "period": filing.tax_period, "arn": arn, "fdate": filing_date_iso},
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
        is_live_portal_filing=is_live_portal_filing,
        filing_mode=filing_mode,
        gsp_reference_id=gsp_ref_id,
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

    claim_row = session.execute(
        text("""
        SELECT original_maker_state, provenance_type, claimed_by_user_id, claim_reason, attestation_nonce, claimed_at
        FROM public.gstr3b_legacy_maker_claims
        WHERE org_id = :org AND filing_id = :fid;
        """),
        {"org": org_id, "fid": str(row.id)}
    ).fetchone()

    legacy_claim = None
    if claim_row:
        legacy_claim = {
            "original_maker_state": claim_row.original_maker_state,
            "provenance_type": claim_row.provenance_type,
            "claimed_by_user_id": str(claim_row.claimed_by_user_id),
            "claim_reason": claim_row.claim_reason,
            "attestation_nonce": claim_row.attestation_nonce,
            "claimed_at": claim_row.claimed_at.isoformat() if claim_row.claimed_at else None,
        }

    if legacy_claim:
        maker_provenance = "LEGACY_CLAIMED"
    elif row.maker_id is None:
        maker_provenance = "UNKNOWN_LEGACY_NULL"
    else:
        maker_provenance = "ESTABLISHED_AT_CREATION"

    return {
        "id": str(row.id),
        "registration_id": str(row.registration_id),
        "tax_period": row.tax_period,
        "filing_status": row.filing_status,
        "revision_number": getattr(row, "revision_number", 1) or 1,
        "previous_snapshot": getattr(row, "previous_snapshot", None),
        "maker_provenance": maker_provenance,
        "legacy_maker_claim": legacy_claim,
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
        "filing_mode": getattr(row, "filing_mode", "SANDBOX") or "SANDBOX",
        "is_live_portal_filing": bool(getattr(row, "is_live_portal_filing", False)),
        "gsp_reference_id": getattr(row, "gsp_reference_id", None),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "disclaimer": GSTR3B_STATUTORY_DISCLAIMER
    }


def get_gstr3b_revision_history(
    session: Session,
    org_id: str,
    filing_id: str
) -> List[Dict[str, Any]]:
    """Retrieves durable, append-only revision history for a GSTR-3B filing."""
    set_tenant_context(session, org_id=org_id, user_id=None)
    rows = session.execute(
        text("""
        SELECT id, filing_id, org_id, revision_number,
               table_3_1_data, table_4_data, offset_data,
               maker_id, checker_id, maker_remarks, checker_remarks, rejected_at, archived_at
        FROM public.gstr3b_revision_history
        WHERE org_id = :org AND filing_id = :fid
        ORDER BY revision_number ASC;
        """),
        {"org": org_id, "fid": filing_id}
    ).fetchall()

    return [
        {
            "id": str(r.id),
            "filing_id": str(r.filing_id),
            "org_id": str(r.org_id),
            "revision_number": r.revision_number,
            "table_3_1": r.table_3_1_data,
            "table_4": r.table_4_data,
            "offset": r.offset_data,
            "maker_id": str(r.maker_id) if r.maker_id else None,
            "checker_id": str(r.checker_id) if r.checker_id else None,
            "maker_remarks": r.maker_remarks,
            "checker_remarks": r.checker_remarks,
            "rejected_at": r.rejected_at.isoformat() if r.rejected_at else None,
            "archived_at": r.archived_at.isoformat() if r.archived_at else None,
        }
        for r in rows
    ]
