import hashlib
import json
import uuid
from decimal import Decimal
from typing import Optional, List, Dict, Any
from datetime import date, datetime

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Header, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.dependencies import get_tenant_db
from app.modules.reconciliation.schemas import (
    GSTR2BParsedStatement, GSTR2BUploadResponse, IMSActionRequest, IMSActionResponse,
    PurchaseDocumentCreate, ReconciliationRunConfig, ReconciliationSummaryOut,
    MatchDetailOut, VarianceDetailOut, MatchCategoryEnum, ITCAssessmentOut,
    HumanResolutionRequest, HumanResolutionResponse, PurchasePaymentRequest,
    GSTR3BTable4WorkingOutput, ITCRiskEvaluateRequest,
    GSTRegistrationCreate, GSTRegistrationOut, GSTR2BRecomputeRequest, GSTR2BRecomputeResponse,
    ITCLedgerEventCreate, ITCLedgerEntryOut, ITCLedgerSummaryOut, IMSActionHistoryOut, ComponentTaxAmount
)
from app.modules.reconciliation.gstr2b_parser import parse_gstr2b_json, parse_gstr2b_excel
from app.modules.reconciliation.purchase_service import (
    create_purchase_document, approve_purchase_document, record_purchase_payment,
    mark_purchase_doc_availed_in_3b,
    DuplicatePurchaseDocumentError, PurchaseServiceError
)
from app.modules.reconciliation.matching_engine import DeterministicReconciliationEngine
from app.modules.reconciliation.itc_engine import StatutoryITCRiskEngine
from app.modules.reconciliation.gstr3b_itc import generate_gstr3b_table4_summary

router = APIRouter(prefix="/reconciliation", tags=["GSTR-2B & Purchase Reconciliation"])


# -----------------------------------------------------------------------------
# 0. Multi-GST Registration Management under PAN
# -----------------------------------------------------------------------------

@router.post("/gst-registrations/create", response_model=GSTRegistrationOut)
def create_gst_registration(
    payload: GSTRegistrationCreate,
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Creates a GST registration under the organization's PAN.
    Validates PAN consistency (chars 3-12 match organization PAN)
    and enforces partial unique index for primary registration.
    """
    org_row = db.execute(
        text("SELECT pan FROM public.organizations WHERE id = :org;"),
        {"org": x_org_id}
    ).fetchone()
    if not org_row or not org_row.pan:
        raise HTTPException(status_code=400, detail="Organization PAN is not configured")

    gstin_clean = payload.gstin.strip().upper()
    extracted_pan = gstin_clean[2:12]
    if extracted_pan != org_row.pan.strip().upper():
        raise HTTPException(
            status_code=400,
            detail=f"GSTIN PAN ({extracted_pan}) does not match organization PAN ({org_row.pan})."
        )

    if payload.is_primary:
        existing_primary = db.execute(
            text("SELECT id FROM public.gst_registrations WHERE org_id = :org AND is_primary = true;"),
            {"org": x_org_id}
        ).fetchone()
        if existing_primary:
            raise HTTPException(
                status_code=409,
                detail="Organization already has a primary GST registration. Only one primary registration is permitted."
            )

    dup_gstin = db.execute(
        text("SELECT id FROM public.gst_registrations WHERE org_id = :org AND gstin = :gstin;"),
        {"org": x_org_id, "gstin": gstin_clean}
    ).fetchone()
    if dup_gstin:
        raise HTTPException(status_code=409, detail=f"GSTIN {gstin_clean} is already registered for this organization.")

    reg_id = str(uuid.uuid4())
    now_ts = datetime.utcnow()

    db.execute(
        text("""
        INSERT INTO public.gst_registrations (
            id, org_id, gstin, state_code, trade_name, legal_name,
            registration_type, is_primary, created_at, updated_at
        ) VALUES (
            :id, :org_id, :gstin, :state_code, :trade_name, :legal_name,
            :reg_type, :is_primary, :ts, :ts
        );
        """),
        {
            "id": reg_id,
            "org_id": x_org_id,
            "gstin": gstin_clean,
            "state_code": payload.state_code,
            "trade_name": payload.trade_name,
            "legal_name": payload.legal_name,
            "reg_type": payload.registration_type,
            "is_primary": payload.is_primary,
            "ts": now_ts
        }
    )
    db.commit()

    return GSTRegistrationOut(
        id=reg_id,
        org_id=x_org_id,
        gstin=gstin_clean,
        state_code=payload.state_code,
        trade_name=payload.trade_name,
        legal_name=payload.legal_name,
        registration_type=payload.registration_type,
        is_primary=payload.is_primary,
        created_at=now_ts.isoformat()
    )


@router.get("/gst-registrations", response_model=List[GSTRegistrationOut])
def list_gst_registrations(
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """Lists all GST registrations belonging to this organization."""
    rows = db.execute(
        text("""
        SELECT id, org_id, gstin, state_code, trade_name, legal_name,
               registration_type, is_primary, created_at
        FROM public.gst_registrations
        WHERE org_id = :org
        ORDER BY is_primary DESC, created_at ASC;
        """),
        {"org": x_org_id}
    ).fetchall()

    return [
        GSTRegistrationOut(
            id=str(r.id),
            org_id=str(r.org_id),
            gstin=r.gstin,
            state_code=r.state_code,
            trade_name=r.trade_name,
            legal_name=r.legal_name,
            registration_type=r.registration_type,
            is_primary=r.is_primary,
            created_at=r.created_at.isoformat() if hasattr(r.created_at, "isoformat") else str(r.created_at)
        )
        for r in rows
    ]


# -----------------------------------------------------------------------------
# 1. GSTR-2B Ingestion & Snapshotting
# -----------------------------------------------------------------------------

@router.post("/gstr2b/upload", response_model=GSTR2BUploadResponse)
def upload_gstr2b(
    registration_id: str = Form(...),
    return_period: str = Form(...),  # YYYY-MM
    file: UploadFile = File(...),
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Ingests GSTR-2B JSON or Excel download. Computes SHA-256 for deduplication,
    verifies GST registration ownership under PAN, creates an immutable statement snapshot,
    and initializes IMS (Invoice Management System) actions for each inward document.
    """
    try:
        # 1. Verify registration
        reg_row = db.execute(
            text("SELECT id, gstin FROM public.gst_registrations WHERE id = :id AND org_id = :org;"),
            {"id": registration_id, "org": x_org_id}
        ).fetchone()
        if not reg_row:
            raise HTTPException(status_code=404, detail="GST Registration not found for this organization")

        content = file.file.read()
        file_hash = hashlib.sha256(content).hexdigest()
        file_size = len(content)
        file_name = file.filename or "gstr2b_statement"

        # 2. Check duplicate hash
        dup_import = db.execute(
            text("SELECT id FROM public.gstr2b_imports WHERE org_id = :org AND file_hash = :hash;"),
            {"org": x_org_id, "hash": file_hash}
        ).fetchone()
        if dup_import:
            raise HTTPException(
                status_code=409,
                detail=f"This GSTR-2B file has already been imported for this organization (hash: {file_hash})."
            )

        # 3. Parse content
        if file_name.endswith(".xlsx") or file_name.endswith(".xls"):
            file_type = "excel"
            parsed: GSTR2BParsedStatement = parse_gstr2b_excel(content)
        else:
            file_type = "json"
            parsed: GSTR2BParsedStatement = parse_gstr2b_json(content)

        # Use parsed return_period if provided in file, else form value
        rp = parsed.return_period if parsed.return_period and "-" in parsed.return_period else return_period
        fy = parsed.financial_year or f"{rp[:4]}-{int(rp[:4])+1}"

        # 4. Insert import record
        import_id = str(uuid.uuid4())
        db.execute(
            text("""
            INSERT INTO public.gstr2b_imports (
                id, org_id, registration_id, recipient_gstin, return_period,
                financial_year, file_name, file_type, file_hash, file_size_bytes,
                parser_version, import_status
            ) VALUES (
                :id, :org_id, :reg_id, :gstin, :rp, :fy, :fn, :ft, :fh, :fs, :pv, 'completed'
            );
            """),
            {
                "id": import_id,
                "org_id": x_org_id,
                "reg_id": registration_id,
                "gstin": reg_row.gstin,
                "rp": rp,
                "fy": fy,
                "fn": file_name,
                "ft": file_type,
                "fh": file_hash,
                "fs": file_size,
                "pv": parsed.parser_version
            }
        )

        # 5. Handle snapshot versioning
        prev_snapshot = db.execute(
            text("""
            SELECT id, version_number FROM public.gstr2b_snapshots
            WHERE org_id = :org AND registration_id = :reg_id AND return_period = :rp AND is_active = true
            ORDER BY version_number DESC LIMIT 1;
            """),
            {"org": x_org_id, "reg_id": registration_id, "rp": rp}
        ).fetchone()

        version_num = 1
        prev_id = None
        if prev_snapshot:
            version_num = prev_snapshot.version_number + 1
            prev_id = prev_snapshot.id
            db.execute(
                text("UPDATE public.gstr2b_snapshots SET is_active = false WHERE id = :id AND org_id = :org;"),
                {"id": prev_snapshot.id, "org": x_org_id}
            )

        snapshot_id = str(uuid.uuid4())
        db.execute(
            text("""
            INSERT INTO public.gstr2b_snapshots (
                id, org_id, import_id, registration_id, recipient_gstin,
                return_period, version_number, previous_snapshot_id,
                total_itc_available, total_itc_not_available, is_active
            ) VALUES (
                :id, :org_id, :import_id, :reg_id, :gstin, :rp, :v_num, :prev_id,
                :itc_avail, :itc_not_avail, true
            );
            """),
            {
                "id": snapshot_id,
                "org_id": x_org_id,
                "import_id": import_id,
                "reg_id": registration_id,
                "gstin": reg_row.gstin,
                "rp": rp,
                "v_num": version_num,
                "prev_id": prev_id,
                "itc_avail": parsed.total_itc_available,
                "itc_not_avail": parsed.total_itc_not_available
            }
        )

        # 6. Insert source documents and initial IMS action
        for doc in parsed.documents:
            doc_id = str(uuid.uuid4())
            db.execute(
                text("""
                INSERT INTO public.gstr2b_source_documents (
                    id, org_id, snapshot_id, section_name, itc_availability,
                    source_form, supplier_gstin, supplier_name, document_type,
                    document_number, document_date, invoice_value, place_of_supply,
                    supply_attract_reverse_charge, taxable_value, integrated_tax,
                    central_tax, state_ut_tax, cess_amount, boe_number, boe_date,
                    port_code, is_amended, raw_payload
                ) VALUES (
                    :id, :org_id, :snapshot_id, :section_name, :itc_availability,
                    :source_form, :supplier_gstin, :supplier_name, :document_type,
                    :document_number, :document_date, :invoice_value, :place_of_supply,
                    :supply_attract_reverse_charge, :taxable_value, :integrated_tax,
                    :central_tax, :state_ut_tax, :cess_amount, :boe_number, :boe_date,
                    :port_code, :is_amended, :raw_payload
                );
                """),
                {
                    "id": doc_id,
                    "org_id": x_org_id,
                    "snapshot_id": snapshot_id,
                    "section_name": doc.section_name,
                    "itc_availability": doc.itc_availability,
                    "source_form": doc.source_form,
                    "supplier_gstin": doc.supplier_gstin,
                    "supplier_name": doc.supplier_name,
                    "document_type": doc.document_type,
                    "document_number": doc.document_number,
                    "document_date": doc.document_date or "2026-11-01",
                    "invoice_value": doc.invoice_value,
                    "place_of_supply": doc.place_of_supply,
                    "supply_attract_reverse_charge": doc.supply_attract_reverse_charge,
                    "taxable_value": doc.taxable_value,
                    "integrated_tax": doc.integrated_tax,
                    "central_tax": doc.central_tax,
                    "state_ut_tax": doc.state_ut_tax,
                    "cess_amount": doc.cess_amount,
                    "boe_number": doc.boe_number,
                    "boe_date": doc.boe_date if doc.boe_date else None,
                    "port_code": doc.port_code,
                    "is_amended": doc.is_amended,
                    "raw_payload": json.dumps(doc.raw_payload) if doc.raw_payload else None
                }
            )

            # Determine IMS initial action: RCM, IMPG, ISD are NOT_AVAILABLE_IN_IMS; others start with NO_ACTION
            initial_ims = "NOT_AVAILABLE_IN_IMS" if (
                doc.supply_attract_reverse_charge
                or "IMPG" in doc.section_name.upper()
                or "ISD" in doc.section_name.upper()
            ) else "NO_ACTION"

            is_deemed = (initial_ims == "NO_ACTION")
            db.execute(
                text("""
                INSERT INTO public.ims_document_actions (
                    id, org_id, gstr2b_doc_id, supplier_doc_reference,
                    current_action, gstr2b_return_period, deemed_accepted_at_generation
                ) VALUES (
                    :id, :org_id, :g_doc_id, :ref, :action, :rp, :deemed
                );
                """),
                {
                    "id": str(uuid.uuid4()),
                    "org_id": x_org_id,
                    "g_doc_id": doc_id,
                    "ref": f"{doc.supplier_gstin or 'UNKNOWN'}:{doc.document_number}",
                    "action": initial_ims,
                    "rp": rp,
                    "deemed": is_deemed
                }
            )

        db.commit()

        return GSTR2BUploadResponse(
            import_id=import_id,
            snapshot_id=snapshot_id,
            version_number=version_num,
            recipient_gstin=reg_row.gstin,
            return_period=rp,
            financial_year=fy,
            total_documents=len(parsed.documents),
            total_itc_available=parsed.total_itc_available,
            total_itc_not_available=parsed.total_itc_not_available,
            file_hash=file_hash
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"GSTR-2B ingestion failed: {str(e)}")


@router.get("/gstr2b/snapshots")
def list_snapshots(
    registration_id: str,
    return_period: Optional[str] = None,
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """Lists snapshots for a GST registration."""
    query = """
    SELECT id, import_id, recipient_gstin, return_period, version_number,
           total_itc_available, total_itc_not_available, is_active, created_at
    FROM public.gstr2b_snapshots
    WHERE org_id = :org AND registration_id = :reg_id
    """
    params = {"org": x_org_id, "reg_id": registration_id}
    if return_period:
        query += " AND return_period = :rp"
        params["rp"] = return_period
    query += " ORDER BY return_period DESC, version_number DESC;"

    rows = db.execute(text(query), params).fetchall()
    return [
        {
            "id": str(r.id),
            "import_id": str(r.import_id),
            "recipient_gstin": r.recipient_gstin,
            "return_period": r.return_period,
            "version_number": r.version_number,
            "total_itc_available": str(r.total_itc_available),
            "total_itc_not_available": str(r.total_itc_not_available),
            "is_active": r.is_active,
            "created_at": r.created_at.isoformat()
        }
        for r in rows
    ]


@router.get("/gstr2b/documents")
def list_gstr2b_documents(
    snapshot_id: str,
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """Lists source documents for a snapshot along with current IMS action."""
    rows = db.execute(
        text("""
        SELECT d.id, d.section_name, d.itc_availability, d.source_form,
               d.supplier_gstin, d.supplier_name, d.document_type, d.document_number,
               d.document_date, d.invoice_value, d.place_of_supply,
               d.supply_attract_reverse_charge, d.taxable_value, d.integrated_tax,
               d.central_tax, d.state_ut_tax, d.cess_amount, d.is_amended,
               COALESCE(i.current_action, 'NO_ACTION') as ims_action,
               COALESCE(i.is_frozen_by_gstr3b, false) as is_frozen_by_gstr3b
        FROM public.gstr2b_source_documents d
        LEFT JOIN public.ims_document_actions i ON d.id = i.gstr2b_doc_id AND d.org_id = i.org_id
        WHERE d.snapshot_id = :sid AND d.org_id = :org
        ORDER BY d.document_date DESC, d.document_number ASC;
        """),
        {"sid": snapshot_id, "org": x_org_id}
    ).fetchall()

    return [
        {
            "id": str(r.id),
            "section_name": r.section_name,
            "itc_availability": r.itc_availability,
            "source_form": r.source_form,
            "supplier_gstin": r.supplier_gstin,
            "supplier_name": r.supplier_name,
            "document_type": r.document_type,
            "document_number": r.document_number,
            "document_date": str(r.document_date),
            "invoice_value": str(r.invoice_value),
            "place_of_supply": r.place_of_supply,
            "supply_attract_reverse_charge": r.supply_attract_reverse_charge,
            "taxable_value": str(r.taxable_value),
            "integrated_tax": str(r.integrated_tax),
            "central_tax": str(r.central_tax),
            "state_ut_tax": str(r.state_ut_tax),
            "cess_amount": str(r.cess_amount),
            "is_amended": r.is_amended,
            "ims_action": r.ims_action,
            "is_frozen_by_gstr3b": r.is_frozen_by_gstr3b
        }
        for r in rows
    ]


# -----------------------------------------------------------------------------
# 2. IMS Action Tracking
# -----------------------------------------------------------------------------

@router.post("/ims/action", response_model=IMSActionResponse)
def set_ims_action(
    payload: IMSActionRequest,
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Updates the IMS (Invoice Management System) action (ACCEPTED, REJECTED, PENDING, NO_ACTION).
    Blocks changes if the statement period has already been filed in GSTR-3B.
    """
    ims_row = db.execute(
        text("""
        SELECT id, current_action, is_frozen_by_gstr3b, gstr2b_return_period
        FROM public.ims_document_actions
        WHERE gstr2b_doc_id = :doc_id AND org_id = :org;
        """),
        {"doc_id": payload.gstr2b_doc_id, "org": x_org_id}
    ).fetchone()

    if not ims_row:
        raise HTTPException(status_code=404, detail="IMS document action record not found")

    if ims_row.is_frozen_by_gstr3b:
        raise HTTPException(
            status_code=403,
            detail=f"IMS actions for period {ims_row.gstr2b_return_period} are frozen because GSTR-3B has been filed."
        )

    # Statutory IMS Rule: Credit Notes cannot be kept Pending by recipient
    if payload.action.value == "PENDING":
        doc_type_row = db.execute(
            text("SELECT document_type, section_name FROM public.gstr2b_source_documents WHERE id = :id AND org_id = :org;"),
            {"id": payload.gstr2b_doc_id, "org": x_org_id}
        ).fetchone()
        if doc_type_row:
            dt = (doc_type_row.document_type or "").lower()
            sec = (doc_type_row.section_name or "").upper()
            if "CREDIT" in dt.upper() or "CRN" in dt.upper() or "CDNR" in sec:
                raise HTTPException(
                    status_code=400,
                    detail="Credit Notes cannot be marked as PENDING in IMS; only ACCEPT or REJECT is permitted by statutory rules."
                )

    prev_action = ims_row.current_action
    now_ts = datetime.utcnow()

    db.execute(
        text("""
        UPDATE public.ims_document_actions
        SET current_action = :act,
            previous_action = :prev,
            action_timestamp = :ts,
            action_by = :user_id,
            updated_at = :ts
        WHERE id = :id AND org_id = :org;
        """),
        {
            "id": ims_row.id,
            "org": x_org_id,
            "act": payload.action.value,
            "prev": prev_action,
            "ts": now_ts,
            "user_id": x_user_id
        }
    )

    # Immutable Audit Log in IMS Action History
    db.execute(
        text("""
        INSERT INTO public.ims_action_history (
            id, org_id, ims_action_id, previous_action, new_action,
            action_reason, actor_id, created_at
        ) VALUES (
            :id, :org, :ims_id, :prev, :new, :reason, :actor, :ts
        );
        """),
        {
            "id": str(uuid.uuid4()),
            "org": x_org_id,
            "ims_id": ims_row.id,
            "prev": prev_action,
            "new": payload.action.value,
            "reason": payload.reason or "Taxpayer action change in IMS",
            "actor": x_user_id,
            "ts": now_ts
        }
    )
    db.commit()

    return IMSActionResponse(
        gstr2b_doc_id=payload.gstr2b_doc_id,
        current_action=payload.action.value,
        previous_action=prev_action,
        action_timestamp=now_ts.isoformat(),
        is_frozen_by_gstr3b=False
    )


@router.post("/gstr2b/recompute", response_model=GSTR2BRecomputeResponse)
def recompute_gstr2b(
    payload: GSTR2BRecomputeRequest,
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Recomputes GSTR-2B statement snapshot after IMS recipient action changes.
    Enforces statutory lifecycle:
    - Blocked if period is frozen by GSTR-3B filing.
    - Excludes 'PENDING' documents from the current period's GSTR-2B available total (held in IMS).
    - Excludes 'REJECTED' documents.
    - Includes 'ACCEPTED' and 'DEEMED_ACCEPTED' / 'NO_ACTION' documents where itc_availability == 'available'.
    - Deactivates previous snapshot and creates Snapshot Version N+1.
    - Preserves action history in ims_document_actions.
    """
    # 1. Check if frozen by GSTR-3B
    frozen_check = db.execute(
        text("""
        SELECT 1 FROM public.ims_document_actions
        WHERE org_id = :org AND gstr2b_return_period = :rp AND is_frozen_by_gstr3b = true
        LIMIT 1;
        """),
        {"org": x_org_id, "rp": payload.return_period}
    ).fetchone()
    if frozen_check:
        raise HTTPException(
            status_code=403,
            detail=f"GSTR-2B recomputation for period {payload.return_period} is blocked because GSTR-3B has already been filed."
        )

    # 2. Fetch current active snapshot
    curr_snapshot = db.execute(
        text("""
        SELECT id, import_id, registration_id, recipient_gstin, return_period, version_number
        FROM public.gstr2b_snapshots
        WHERE id = :sid AND org_id = :org;
        """),
        {"sid": payload.snapshot_id, "org": x_org_id}
    ).fetchone()
    if not curr_snapshot:
        raise HTTPException(status_code=404, detail="GSTR-2B snapshot not found")

    # 3. Fetch all source documents and their latest IMS actions
    doc_rows = db.execute(
        text("""
        SELECT d.id, d.itc_availability, d.taxable_value, d.integrated_tax,
               d.central_tax, d.state_ut_tax, d.cess_amount,
               COALESCE(a.current_action, 'NO_ACTION') as ims_action
        FROM public.gstr2b_source_documents d
        LEFT JOIN public.ims_document_actions a ON d.id = a.gstr2b_doc_id AND d.org_id = a.org_id
        WHERE d.snapshot_id = :sid AND d.org_id = :org;
        """),
        {"sid": payload.snapshot_id, "org": x_org_id}
    ).fetchall()

    new_avail = Decimal("0.00")
    new_not_avail = Decimal("0.00")
    accepted_count = 0
    pending_count = 0
    rejected_count = 0

    for d in doc_rows:
        tax = Decimal(str((d.integrated_tax or 0) + (d.central_tax or 0) + (d.state_ut_tax or 0) + (d.cess_amount or 0)))
        act = d.ims_action

        if d.itc_availability == "not_available":
            new_not_avail += tax
        else:
            if act in ("ACCEPTED", "DEEMED_ACCEPTED", "NO_ACTION"):
                new_avail += tax
                accepted_count += 1
            elif act == "PENDING":
                pending_count += 1
                # Statutory rule: Pending records do NOT flow into generated GSTR-2B (held in IMS)
            elif act == "REJECTED":
                rejected_count += 1
                new_not_avail += tax
            else:
                new_avail += tax
                accepted_count += 1

    # 4. Deactivate old snapshot
    db.execute(
        text("UPDATE public.gstr2b_snapshots SET is_active = false WHERE id = :id AND org_id = :org;"),
        {"id": curr_snapshot.id, "org": x_org_id}
    )

    # 5. Insert new Snapshot Version N+1
    new_version = curr_snapshot.version_number + 1
    new_snap_id = str(uuid.uuid4())
    now_ts = datetime.utcnow()

    db.execute(
        text("""
        INSERT INTO public.gstr2b_snapshots (
            id, org_id, import_id, registration_id, recipient_gstin,
            return_period, version_number, previous_snapshot_id,
            total_itc_available, total_itc_not_available, is_active
        ) VALUES (
            :id, :org_id, :import_id, :reg_id, :gstin,
            :rp, :ver, :prev_id, :avail, :not_avail, true
        );
        """),
        {
            "id": new_snap_id,
            "org_id": x_org_id,
            "import_id": curr_snapshot.import_id,
            "reg_id": curr_snapshot.registration_id,
            "gstin": curr_snapshot.recipient_gstin,
            "rp": curr_snapshot.return_period,
            "ver": new_version,
            "prev_id": curr_snapshot.id,
            "avail": new_avail,
            "not_avail": new_not_avail
        }
    )

    # 6. Mark ims_document_actions as recomputed
    db.execute(
        text("""
        UPDATE public.ims_document_actions
        SET recomputed_at = :ts
        WHERE org_id = :org AND gstr2b_return_period = :rp;
        """),
        {"org": x_org_id, "rp": curr_snapshot.return_period, "ts": now_ts}
    )

    db.commit()

    return GSTR2BRecomputeResponse(
        new_snapshot_id=new_snap_id,
        previous_snapshot_id=str(curr_snapshot.id),
        version_number=new_version,
        return_period=curr_snapshot.return_period,
        recipient_gstin=curr_snapshot.recipient_gstin,
        total_itc_available=new_avail,
        total_itc_not_available=new_not_avail,
        accepted_docs_count=accepted_count,
        pending_docs_held_count=pending_count,
        rejected_docs_count=rejected_count,
        recomputed_at=now_ts.isoformat()
    )


@router.post("/gstr3b/file-freeze")
def freeze_ims_on_gstr3b_filing(
    registration_id: str = Query(...),
    return_period: str = Query(...),
    filing_status: str = Query("FILED", description="Must be confirmed FILED; drafts or submitted attempts do not freeze IMS"),
    filing_arn: Optional[str] = Query(None, description="GSTN Application Reference Number / Acknowledgement"),
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Freezes all IMS actions and GSTR-2B snapshots for a specific registration and return period upon confirmed GSTR-3B filing.
    Prevents any further action modification or GSTR-2B recomputation.
    Rejects freeze if filing_status is not 'FILED'.
    """
    if filing_status.upper() != "FILED":
        raise HTTPException(
            status_code=400,
            detail="Freeze rejected: IMS actions can only be frozen upon confirmed filing (filing_status 'FILED'). Draft or pending submission status does not freeze IMS actions."
        )

    now_ts = datetime.utcnow()
    res = db.execute(
        text("""
        UPDATE public.ims_document_actions
        SET is_frozen_by_gstr3b = true,
            gstr3b_filing_date = :ts,
            updated_at = :ts
        WHERE org_id = :org AND gstr2b_return_period = :rp;
        """),
        {"org": x_org_id, "rp": return_period, "ts": now_ts}
    )

    db.execute(
        text("""
        UPDATE public.gstr2b_snapshots
        SET is_frozen = true,
            filing_confirmation_arn = :arn,
            filed_at = :ts
        WHERE org_id = :org AND return_period = :rp AND is_active = true;
        """),
        {"org": x_org_id, "rp": return_period, "arn": filing_arn, "ts": now_ts}
    )
    db.commit()
    return {
        "status": "frozen",
        "return_period": return_period,
        "filing_status": "FILED",
        "filing_arn": filing_arn,
        "frozen_timestamp": now_ts.isoformat(),
        "rows_affected": res.rowcount
    }


# -----------------------------------------------------------------------------
# 3. Independent Purchase Register
# -----------------------------------------------------------------------------

@router.post("/purchase/create")
def create_purchase(
    payload: PurchaseDocumentCreate,
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """Creates a new inward purchase document in the purchase register."""
    try:
        res = create_purchase_document(session=db, org_id=x_org_id, payload=payload, user_id=x_user_id)
        db.commit()
        return res
    except DuplicatePurchaseDocumentError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    except PurchaseServiceError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create purchase document: {str(e)}")


@router.get("/purchase/documents")
def list_purchase_documents(
    registration_id: str,
    financial_period: Optional[str] = None,
    status: Optional[str] = None,
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """Lists purchase register documents."""
    query = """
    SELECT id, registration_id, document_type, document_number, document_date,
           financial_period, supplier_gstin, supplier_legal_name, recipient_gstin,
           place_of_supply, is_reverse_charge, supply_category, total_taxable_amount,
           cgst_amount, sgst_amount, igst_amount, cess_amount, total_invoice_value,
           goods_services_type, status, created_at
    FROM public.purchase_documents
    WHERE org_id = :org AND registration_id = :reg_id
    """
    params = {"org": x_org_id, "reg_id": registration_id}
    if financial_period:
        query += " AND financial_period = :fp"
        params["fp"] = financial_period
    if status:
        query += " AND status = :st"
        params["st"] = status
    query += " ORDER BY document_date DESC, document_number ASC;"

    rows = db.execute(text(query), params).fetchall()
    return [
        {
            "id": str(r.id),
            "registration_id": str(r.registration_id),
            "document_type": r.document_type,
            "document_number": r.document_number,
            "document_date": str(r.document_date),
            "financial_period": r.financial_period,
            "supplier_gstin": r.supplier_gstin,
            "supplier_legal_name": r.supplier_legal_name,
            "recipient_gstin": r.recipient_gstin,
            "place_of_supply": r.place_of_supply,
            "is_reverse_charge": r.is_reverse_charge,
            "supply_category": r.supply_category,
            "total_taxable_amount": str(r.total_taxable_amount) if r.total_taxable_amount is not None else None,
            "cgst_amount": str(r.cgst_amount) if r.cgst_amount is not None else None,
            "sgst_amount": str(r.sgst_amount) if r.sgst_amount is not None else None,
            "igst_amount": str(r.igst_amount) if r.igst_amount is not None else None,
            "cess_amount": str(r.cess_amount) if r.cess_amount is not None else None,
            "total_invoice_value": str(r.total_invoice_value) if r.total_invoice_value is not None else None,
            "goods_services_type": r.goods_services_type,
            "status": r.status,
            "created_at": r.created_at.isoformat()
        }
        for r in rows
    ]


@router.post("/purchase/{id}/approve")
def approve_purchase(
    id: str,
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """Approves a purchase document after human review."""
    try:
        res = approve_purchase_document(session=db, org_id=x_org_id, purchase_doc_id=id, user_id=x_user_id)
        db.commit()
        return res
    except PurchaseServiceError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/purchase/payment")
def add_purchase_payment(
    payload: PurchasePaymentRequest,
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """Records payment and allocates to purchase documents."""
    try:
        alloc_list = [a.dict() for a in payload.allocations]
        res = record_purchase_payment(
            session=db,
            org_id=x_org_id,
            payment_reference=payload.payment_reference,
            payment_date=payload.payment_date,
            payment_mode=payload.payment_mode,
            total_amount_paid=payload.total_amount_paid,
            bank_reference=payload.bank_reference,
            allocations=alloc_list
        )
        db.commit()
        return res
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to record payment: {str(e)}")


# -----------------------------------------------------------------------------
# 4. Deterministic 4-Pass Reconciliation Engine
# -----------------------------------------------------------------------------

@router.post("/run", response_model=ReconciliationSummaryOut)
def run_reconciliation(
    config: ReconciliationRunConfig,
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Executes the deterministic 4-pass reconciliation between Books (Purchase Register)
    and GST Portal (GSTR-2B Statement).
    """
    # 1. Fetch GSTR-2B documents for snapshot
    gstr2b_rows = db.execute(
        text("""
        SELECT id, section_name, itc_availability, source_form, supplier_gstin,
               supplier_name, document_type, document_number, document_date,
               invoice_value, place_of_supply, supply_attract_reverse_charge,
               taxable_value, integrated_tax, central_tax, state_ut_tax, cess_amount,
               is_amended, original_document_number, original_document_date
        FROM public.gstr2b_source_documents
        WHERE snapshot_id = :sid AND org_id = :org;
        """),
        {"sid": config.snapshot_id, "org": x_org_id}
    ).fetchall()

    gstr2b_docs = [dict(r._mapping) for r in gstr2b_rows]

    # 2. Fetch Purchase Register documents for registration & period
    purchase_rows = db.execute(
        text("""
        SELECT id, registration_id, document_type, document_number, document_date,
               financial_period, supplier_gstin, supplier_legal_name, recipient_gstin,
               place_of_supply, is_reverse_charge, supply_category, total_taxable_amount,
               cgst_amount, sgst_amount, igst_amount, cess_amount, total_invoice_value,
               goods_services_type, status
        FROM public.purchase_documents
        WHERE registration_id = :reg_id AND financial_period = :rp AND org_id = :org;
        """),
        {"reg_id": config.registration_id, "rp": config.return_period, "org": x_org_id}
    ).fetchall()

    purchase_docs = [dict(r._mapping) for r in purchase_rows]

    # 3. Run Matching Engine
    engine = DeterministicReconciliationEngine(
        taxable_tolerance=config.taxable_tolerance,
        tax_tolerance=config.tax_tolerance,
        total_tolerance=config.total_tolerance
    )
    matches, variances = engine.run_reconciliation(purchase_docs, gstr2b_docs)

    # 4. Count metrics
    matched_cnt = len(matches)
    variance_cnt = len(variances)
    missing_gstr2b_cnt = sum(1 for v in variances if v.variance_category == MatchCategoryEnum.MISSING_IN_GSTR2B)
    missing_books_cnt = sum(1 for v in variances if v.variance_category == MatchCategoryEnum.MISSING_IN_BOOKS)

    # 5. Persist run
    run_id = str(uuid.uuid4())
    db.execute(
        text("""
        INSERT INTO public.reconciliation_runs (
            id, org_id, registration_id, return_period, snapshot_id,
            engine_version, taxable_tolerance, tax_tolerance, total_tolerance,
            matched_count, variance_count, missing_in_gstr2b_count,
            missing_in_books_count, run_status
        ) VALUES (
            :id, :org_id, :reg_id, :rp, :sid, :ev, :ttol, :tax_tol, :tot_tol,
            :mc, :vc, :mgc, :mbc, 'completed'
        );
        """),
        {
            "id": run_id,
            "org_id": x_org_id,
            "reg_id": config.registration_id,
            "rp": config.return_period,
            "sid": config.snapshot_id,
            "ev": engine.engine_version,
            "ttol": config.taxable_tolerance,
            "tax_tol": config.tax_tolerance,
            "tot_tol": config.total_tolerance,
            "mc": matched_cnt,
            "vc": variance_cnt,
            "mgc": missing_gstr2b_cnt,
            "mbc": missing_books_cnt
        }
    )

    # Persist matches
    for m in matches:
        db.execute(
            text("""
            INSERT INTO public.reconciliation_matches (
                id, org_id, run_id, purchase_doc_id, gstr2b_doc_id, match_pass,
                match_score, is_exact, taxable_difference, tax_difference,
                total_difference, match_evidence
            ) VALUES (
                :id, :org_id, :run_id, :pid, :gid, :mp, :ms, :exact,
                :taxable_diff, :tax_diff, :total_diff, :evidence
            );
            """),
            {
                "id": str(uuid.uuid4()),
                "org_id": x_org_id,
                "run_id": run_id,
                "pid": m.purchase_doc_id,
                "gid": m.gstr2b_doc_id,
                "mp": m.match_pass,
                "ms": m.match_score,
                "exact": m.is_exact,
                "taxable_diff": m.taxable_difference,
                "tax_diff": m.tax_difference,
                "total_diff": m.total_difference,
                "evidence": json.dumps(m.match_evidence)
            }
        )

    # Persist variances
    for v in variances:
        db.execute(
            text("""
            INSERT INTO public.reconciliation_variances (
                id, org_id, run_id, variance_category, purchase_doc_id,
                gstr2b_doc_id, difference_details
            ) VALUES (
                :id, :org_id, :run_id, :cat, :pid, :gid, :diff
            );
            """),
            {
                "id": str(uuid.uuid4()),
                "org_id": x_org_id,
                "run_id": run_id,
                "cat": v.variance_category.value,
                "pid": v.purchase_doc_id,
                "gid": v.gstr2b_doc_id,
                "diff": json.dumps(v.difference_details)
            }
        )

    db.commit()

    return ReconciliationSummaryOut(
        run_id=run_id,
        return_period=config.return_period,
        matched_count=matched_cnt,
        variance_count=variance_cnt,
        missing_in_gstr2b_count=missing_gstr2b_cnt,
        missing_in_books_count=missing_books_cnt,
        matches=matches,
        variances=variances
    )


# -----------------------------------------------------------------------------
# 5. Versioned Statutory ITC Risk & Eligibility Engine
# -----------------------------------------------------------------------------

@router.post("/itc-risk/evaluate", response_model=List[ITCAssessmentOut])
def evaluate_itc_risk(
    payload: ITCRiskEvaluateRequest,
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Evaluates statutory ITC risk for a purchase document (Section 16, 17, Rule 37 180 days, Rule 37A).
    """
    p_row = db.execute(
        text("SELECT * FROM public.purchase_documents WHERE id = :id AND org_id = :org;"),
        {"id": payload.purchase_doc_id, "org": x_org_id}
    ).fetchone()
    if not p_row:
        raise HTTPException(status_code=404, detail="Purchase document not found")

    purchase_doc = dict(p_row._mapping)

    gstr2b_doc = None
    if payload.gstr2b_doc_id:
        g_row = db.execute(
            text("SELECT * FROM public.gstr2b_source_documents WHERE id = :id AND org_id = :org;"),
            {"id": payload.gstr2b_doc_id, "org": x_org_id}
        ).fetchone()
        if g_row:
            gstr2b_doc = dict(g_row._mapping)

    # Fetch payments
    pay_rows = db.execute(
        text("""
        SELECT a.allocated_taxable_amount, a.allocated_tax_amount, a.allocated_total_amount,
               p.payment_date, p.payment_reference
        FROM public.purchase_payment_allocations a
        JOIN public.purchase_payments p ON a.payment_id = p.id AND a.org_id = p.org_id
        WHERE a.purchase_doc_id = :id AND a.org_id = :org;
        """),
        {"id": payload.purchase_doc_id, "org": x_org_id}
    ).fetchall()
    payments = [dict(r._mapping) for r in pay_rows]

    eval_date = datetime.strptime(payload.evaluation_date, "%Y-%m-%d").date() if payload.evaluation_date else date.today()

    engine = StatutoryITCRiskEngine()
    assessments = engine.evaluate_itc_eligibility(
        purchase_doc=purchase_doc,
        gstr2b_doc=gstr2b_doc,
        payments=payments,
        evaluation_date=eval_date
    )

    # Persist assessments
    for a in assessments:
        db.execute(
            text("""
            INSERT INTO public.itc_assessments (
                id, org_id, purchase_doc_id, gstr2b_doc_id, rule_code,
                eligibility_status, potential_itc_cgst, potential_itc_sgst,
                potential_itc_igst, potential_itc_cess, risk_reversal_amount,
                evaluation_date, findings_explanation, missing_evidence_required,
                evaluated_by_engine
            ) VALUES (
                :id, :org_id, :pid, :gid, :rc, :es, :cgst, :sgst, :igst, :cess,
                :risk, :eval_dt, :exp, :ev_req, :engine
            );
            """),
            {
                "id": str(uuid.uuid4()),
                "org_id": x_org_id,
                "pid": a.purchase_doc_id,
                "gid": a.gstr2b_doc_id,
                "rc": a.rule_code,
                "es": a.eligibility_status.value,
                "cgst": Decimal("0.00"),
                "sgst": Decimal("0.00"),
                "igst": Decimal("0.00"),
                "cess": Decimal("0.00"),
                "risk": a.risk_reversal_amount,
                "eval_dt": a.evaluation_date,
                "exp": a.findings_explanation,
                "ev_req": a.missing_evidence_required,
                "engine": engine.engine_version
            }
        )

    db.commit()
    return assessments


# -----------------------------------------------------------------------------
# 6. Human Resolution Workbench
# -----------------------------------------------------------------------------

@router.post("/resolve", response_model=HumanResolutionResponse)
def resolve_variance(
    payload: HumanResolutionRequest,
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Records an auditor/CA human resolution event for reconciliation variances or blocked credit states.
    """
    res_id = str(uuid.uuid4())
    now_ts = datetime.utcnow()

    db.execute(
        text("""
        INSERT INTO public.human_resolution_events (
            id, org_id, variance_id, purchase_doc_id, gstr2b_doc_id,
            resolution_action, resolution_notes, supporting_document_id,
            resolved_by, created_at
        ) VALUES (
            :id, :org_id, :vid, :pid, :gid, :act, :notes, :doc_id, :user_id, :created_at
        );
        """),
        {
            "id": res_id,
            "org_id": x_org_id,
            "vid": payload.variance_id,
            "pid": payload.purchase_doc_id,
            "gid": payload.gstr2b_doc_id,
            "act": payload.resolution_action,
            "notes": payload.resolution_notes,
            "doc_id": payload.supporting_document_id,
            "user_id": x_user_id,
            "created_at": now_ts
        }
    )
    db.commit()

    return HumanResolutionResponse(
        id=res_id,
        resolution_action=payload.resolution_action,
        resolved_by=x_user_id,
        created_at=now_ts.isoformat()
    )


# -----------------------------------------------------------------------------
# 7. GSTR-3B Table 4 Inward ITC Working Summary
# -----------------------------------------------------------------------------

@router.get("/gstr3b-table4-working-summary", response_model=GSTR3BTable4WorkingOutput)
def get_gstr3b_table4_summary(
    registration_id: str,
    return_period: str,
    snapshot_id: str,
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Generates statutory GSTR-3B Table 4 Inward ITC Working Output.
    Distinctly calculates and separates:
    1. GSTR-2B Statement Total Available
    2. Book Matched Total
    3. Legally Eligible Total
    4. Human Approved Total
    """
    # 1. Registration
    reg_row = db.execute(
        text("SELECT gstin FROM public.gst_registrations WHERE id = :id AND org_id = :org;"),
        {"id": registration_id, "org": x_org_id}
    ).fetchone()
    if not reg_row:
        raise HTTPException(status_code=404, detail="GST Registration not found")

    # 2. GSTR-2B docs
    g_rows = db.execute(
        text("SELECT * FROM public.gstr2b_source_documents WHERE snapshot_id = :sid AND org_id = :org;"),
        {"sid": snapshot_id, "org": x_org_id}
    ).fetchall()
    gstr2b_docs = [dict(r._mapping) for r in g_rows]

    # 3. Purchase docs
    p_rows = db.execute(
        text("""
        SELECT * FROM public.purchase_documents
        WHERE registration_id = :reg_id AND financial_period = :rp AND org_id = :org;
        """),
        {"reg_id": registration_id, "rp": return_period, "org": x_org_id}
    ).fetchall()
    purchase_docs = [dict(r._mapping) for r in p_rows]

    # 4. Matches
    m_rows = db.execute(
        text("""
        SELECT m.purchase_doc_id, m.gstr2b_doc_id, m.match_pass, m.is_exact
        FROM public.reconciliation_matches m
        JOIN public.reconciliation_runs r ON m.run_id = r.id AND m.org_id = r.org_id
        WHERE r.registration_id = :reg_id AND r.return_period = :rp AND r.snapshot_id = :sid AND m.org_id = :org;
        """),
        {"reg_id": registration_id, "rp": return_period, "sid": snapshot_id, "org": x_org_id}
    ).fetchall()
    matches = [dict(r._mapping) for r in m_rows]

    # 5. Assessments
    a_rows = db.execute(
        text("""
        SELECT a.purchase_doc_id, a.gstr2b_doc_id, a.rule_code, a.eligibility_status, a.risk_reversal_amount
        FROM public.itc_assessments a
        JOIN public.purchase_documents p ON a.purchase_doc_id = p.id AND a.org_id = p.org_id
        WHERE p.registration_id = :reg_id AND p.financial_period = :rp AND a.org_id = :org;
        """),
        {"reg_id": registration_id, "rp": return_period, "org": x_org_id}
    ).fetchall()
    assessments = [dict(r._mapping) for r in a_rows]

    # 6. Approved IDs
    approved_rows = db.execute(
        text("SELECT id FROM public.purchase_documents WHERE registration_id = :reg_id AND status = 'approved' AND org_id = :org;"),
        {"reg_id": registration_id, "org": x_org_id}
    ).fetchall()
    approved_set = {str(r.id) for r in approved_rows}

    # 7. Reclaims / Reavailment Events
    rec_rows = db.execute(
        text("""
        SELECT r.*
        FROM public.itc_reavailment_events r
        JOIN public.purchase_documents p ON r.purchase_doc_id = p.id AND r.org_id = p.org_id
        WHERE p.registration_id = :reg_id AND r.reavailment_period = :rp AND r.org_id = :org;
        """),
        {"reg_id": registration_id, "rp": return_period, "org": x_org_id}
    ).fetchall()
    reclaims = [dict(r._mapping) for r in rec_rows]

    # 8. Reversals Ledger Events
    rev_rows = db.execute(
        text("""
        SELECT r.*
        FROM public.itc_reversal_events r
        JOIN public.purchase_documents p ON r.purchase_doc_id = p.id AND r.org_id = p.org_id
        WHERE p.registration_id = :reg_id AND r.reversal_period = :rp AND r.org_id = :org;
        """),
        {"reg_id": registration_id, "rp": return_period, "org": x_org_id}
    ).fetchall()
    reversals_ledger = [dict(r._mapping) for r in rev_rows]

    return generate_gstr3b_table4_summary(
        return_period=return_period,
        recipient_gstin=reg_row.gstin,
        gstr2b_docs=gstr2b_docs,
        purchase_docs=purchase_docs,
        matches=matches,
        assessments=assessments,
        human_approved_ids=approved_set,
        reclaims=reclaims,
        reversals_ledger=reversals_ledger
    )


# -----------------------------------------------------------------------------
# 8. Purchase Register GSTR-3B Availment Tracking (Rule 37A)
# -----------------------------------------------------------------------------

@router.post("/purchase/avail-in-3b")
def avail_purchase_document_in_3b(
    purchase_doc_id: str = Query(..., description="Purchase document UUID"),
    return_period: str = Query(..., description="GSTR-3B tax period YYYY-MM"),
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Marks an inward purchase document as availed in GSTR-3B for a specific return period.
    Statutory prerequisite for assessing Rule 37A supplier non-filing reversal risk.
    """
    res = mark_purchase_doc_availed_in_3b(
        session=db,
        org_id=x_org_id,
        doc_id=purchase_doc_id,
        return_period=return_period
    )
    db.commit()
    return res


# -----------------------------------------------------------------------------
# 9. Traceable Event-Based ITC Ledger (Availment, Reversals & Reclaims)
# -----------------------------------------------------------------------------

@router.post("/itc-ledger/record-event", response_model=ITCLedgerEntryOut)
def record_itc_ledger_event(
    payload: ITCLedgerEventCreate,
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Records an immutable, event-based ITC lifecycle event (AVAIL, REVERSAL_*, RECLAIM).
    Enforces statutory accounting rules:
    - Reversals require that the document was actually availed in GSTR-3B (Section 16(2) / Rule 37A).
    - Reverse charge supplies cannot be reversed under Rule 37 (Section 16(2) 2nd proviso).
    - No duplicate reversal for the same rule/document in the same period.
    - Reclaims cannot exceed previously reversed amounts.
    - Permanent Section 17(5) and Rule 38 credit cannot be reavailed.
    - Reclaim must reference an existing reversal event.
    """
    p_row = db.execute(
        text("SELECT * FROM public.purchase_documents WHERE id = :id AND org_id = :org;"),
        {"id": payload.purchase_doc_id, "org": x_org_id}
    ).fetchone()
    if not p_row:
        raise HTTPException(status_code=404, detail="Purchase document not found")
    p_doc = dict(p_row._mapping)

    total_event_tax = payload.cgst_amount + payload.sgst_amount + payload.igst_amount + payload.cess_amount
    now_ts = datetime.utcnow()
    rev_event_id = payload.reversal_event_id

    if payload.event_type == "AVAIL":
        db.execute(
            text("""
            UPDATE public.purchase_documents
            SET is_availed_in_3b = true,
                availed_period = :rp,
                updated_at = :ts
            WHERE id = :id AND org_id = :org;
            """),
            {"id": p_doc["id"], "org": x_org_id, "rp": payload.tax_period, "ts": now_ts}
        )

    elif payload.event_type.startswith("REVERSAL_"):
        if not p_doc.get("is_availed_in_3b"):
            raise HTTPException(
                status_code=400,
                detail="Statutory Violation: Cannot record reversal for an invoice that has not been availed in GSTR-3B."
            )

        if payload.event_type == "REVERSAL_RULE_37" and p_doc.get("is_reverse_charge"):
            raise HTTPException(
                status_code=400,
                detail="Statutory Violation: Reverse-charge supplies are explicitly excluded from 180-day payment reversals under Section 16(2) second proviso."
            )

        dup_rev = db.execute(
            text("""
            SELECT id FROM public.itc_ledger_entries
            WHERE purchase_doc_id = :pid AND event_type = :etype AND tax_period = :rp AND org_id = :org;
            """),
            {"pid": p_doc["id"], "etype": payload.event_type, "rp": payload.tax_period, "org": x_org_id}
        ).fetchone()
        if dup_rev:
            raise HTTPException(
                status_code=400,
                detail=f"Duplicate reversal rejected: {payload.event_type} already recorded for period {payload.tax_period}."
            )

        rule_map = {
            "REVERSAL_RULE_37": "RULE_37_180_DAYS",
            "REVERSAL_RULE_37A": "RULE_37A_NON_FILING",
            "REVERSAL_PERMANENT_17_5": "SEC_17_5_MOTOR",
            "REVERSAL_RULE_38": "RULE_38",
            "REVERSAL_RULE_42_43": "RULE_42",
            "REVERSAL_OTHER_TEMP": "SEC_16_4"
        }
        r_code = rule_map.get(payload.event_type, "RULE_37_180_DAYS")

        rev_id = str(uuid.uuid4())
        db.execute(
            text("""
            INSERT INTO public.itc_reversal_events (
                id, org_id, purchase_doc_id, rule_code, reversal_reason,
                reversal_period, reversed_taxable, reversed_cgst, reversed_sgst,
                reversed_igst, reversed_cess, interest_liability, interest_basis, created_at
            ) VALUES (
                :id, :org, :pid, :rule, :reason,
                :rp, :taxable, :cgst, :sgst,
                :igst, :cess, :interest, :int_basis, :ts
            );
            """),
            {
                "id": rev_id,
                "org": x_org_id,
                "pid": p_doc["id"],
                "rule": r_code,
                "reason": payload.reversal_reason or f"Reversal under {payload.event_type}",
                "rp": payload.tax_period,
                "taxable": Decimal("0.00"),
                "cgst": payload.cgst_amount,
                "sgst": payload.sgst_amount,
                "igst": payload.igst_amount,
                "cess": payload.cess_amount,
                "interest": payload.interest_amount,
                "int_basis": payload.interest_basis or "Section 50(3) conditional on utilization",
                "ts": now_ts
            }
        )
        rev_event_id = rev_id

    elif payload.event_type == "RECLAIM":
        if not payload.reversal_event_id:
            raise HTTPException(
                status_code=400,
                detail="Reclaim must reference original reversal_event_id."
            )

        orig_rev = db.execute(
            text("SELECT * FROM public.itc_reversal_events WHERE id = :id AND org_id = :org;"),
            {"id": payload.reversal_event_id, "org": x_org_id}
        ).fetchone()
        if not orig_rev:
            raise HTTPException(status_code=404, detail="Referenced reversal event not found.")

        rule_code = orig_rev.rule_code or ""
        if rule_code.startswith("SEC_17_5") or rule_code in ("RULE_38", "RULE_42", "RULE_43"):
            raise HTTPException(
                status_code=400,
                detail=f"Statutory Reclaim Prohibited: Reversal under {rule_code} is permanent and non-reclaimable."
            )

        tot_rev = Decimal(str(
            (orig_rev.reversed_cgst or 0) + (orig_rev.reversed_sgst or 0) +
            (orig_rev.reversed_igst or 0) + (orig_rev.reversed_cess or 0)
        ))
        already_reavailed = db.execute(
            text("""
            SELECT COALESCE(SUM(reavailed_cgst + reavailed_sgst + reavailed_igst + reavailed_cess), 0) as tot
            FROM public.itc_reavailment_events
            WHERE reversal_event_id = :rev_id AND org_id = :org;
            """),
            {"rev_id": payload.reversal_event_id, "org": x_org_id}
        ).scalar() or Decimal("0.00")

        if Decimal(str(already_reavailed)) + total_event_tax > tot_rev:
            raise HTTPException(
                status_code=400,
                detail=f"Reclaim bounds exceeded: Cumulative reclaims (₹{already_reavailed + total_event_tax}) cannot exceed original reversal (₹{tot_rev})."
            )

        reclaim_id = str(uuid.uuid4())
        db.execute(
            text("""
            INSERT INTO public.itc_reavailment_events (
                id, org_id, purchase_doc_id, reversal_event_id, reavailment_period,
                reavailed_cgst, reavailed_sgst, reavailed_igst, reavailed_cess,
                reavailment_reason, created_at
            ) VALUES (
                :id, :org, :pid, :rev_id, :rp,
                :cgst, :sgst, :igst, :cess,
                :reason, :ts
            );
            """),
            {
                "id": reclaim_id,
                "org": x_org_id,
                "pid": p_doc["id"],
                "rev_id": payload.reversal_event_id,
                "rp": payload.tax_period,
                "cgst": payload.cgst_amount,
                "sgst": payload.sgst_amount,
                "igst": payload.igst_amount,
                "cess": payload.cess_amount,
                "reason": payload.reversal_reason or "Statutory re-availment upon condition fulfillment",
                "ts": now_ts
            }
        )

    entry_id = str(uuid.uuid4())
    prov = payload.provenance or {
        "supplier_gstin": p_doc.get("supplier_gstin"),
        "document_number": p_doc.get("document_number"),
        "document_date": str(p_doc.get("document_date")),
        "actor_id": x_user_id
    }

    db.execute(
        text("""
        INSERT INTO public.itc_ledger_entries (
            id, org_id, purchase_doc_id, gstr2b_doc_id, event_type,
            tax_period, cgst_amount, sgst_amount, igst_amount, cess_amount,
            total_tax, reversal_event_id, interest_amount, interest_basis,
            provenance, created_at
        ) VALUES (
            :id, :org, :pid, :gid, :etype,
            :rp, :cgst, :sgst, :igst, :cess,
            :tot, :rev_id, :interest, :int_basis,
            :prov, :ts
        );
        """),
        {
            "id": entry_id,
            "org": x_org_id,
            "pid": p_doc["id"],
            "gid": payload.gstr2b_doc_id,
            "etype": payload.event_type,
            "rp": payload.tax_period,
            "cgst": payload.cgst_amount,
            "sgst": payload.sgst_amount,
            "igst": payload.igst_amount,
            "cess": payload.cess_amount,
            "tot": total_event_tax,
            "rev_id": rev_event_id,
            "interest": payload.interest_amount,
            "int_basis": payload.interest_basis,
            "prov": json.dumps(prov),
            "ts": now_ts
        }
    )

    db.commit()

    return ITCLedgerEntryOut(
        id=entry_id,
        purchase_doc_id=payload.purchase_doc_id,
        gstr2b_doc_id=payload.gstr2b_doc_id,
        event_type=payload.event_type,
        tax_period=payload.tax_period,
        cgst_amount=payload.cgst_amount,
        sgst_amount=payload.sgst_amount,
        igst_amount=payload.igst_amount,
        cess_amount=payload.cess_amount,
        total_tax=total_event_tax,
        reversal_event_id=rev_event_id,
        interest_amount=payload.interest_amount,
        interest_basis=payload.interest_basis,
        provenance=prov,
        created_at=now_ts.isoformat()
    )


@router.get("/itc-ledger/{purchase_doc_id}", response_model=List[ITCLedgerEntryOut])
def get_purchase_doc_itc_history(
    purchase_doc_id: str,
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Returns full chronological ITC lifecycle audit events for a purchase document.
    """
    rows = db.execute(
        text("""
        SELECT * FROM public.itc_ledger_entries
        WHERE purchase_doc_id = :pid AND org_id = :org
        ORDER BY created_at ASC;
        """),
        {"pid": purchase_doc_id, "org": x_org_id}
    ).fetchall()
    return [
        ITCLedgerEntryOut(
            id=str(r.id),
            purchase_doc_id=str(r.purchase_doc_id),
            gstr2b_doc_id=str(r.gstr2b_doc_id) if r.gstr2b_doc_id else None,
            event_type=r.event_type,
            tax_period=r.tax_period,
            cgst_amount=Decimal(str(r.cgst_amount or 0)),
            sgst_amount=Decimal(str(r.sgst_amount or 0)),
            igst_amount=Decimal(str(r.igst_amount or 0)),
            cess_amount=Decimal(str(r.cess_amount or 0)),
            total_tax=Decimal(str(r.total_tax or 0)),
            reversal_event_id=str(r.reversal_event_id) if r.reversal_event_id else None,
            interest_amount=Decimal(str(r.interest_amount or 0)),
            interest_basis=r.interest_basis,
            provenance=r.provenance or {},
            created_at=r.created_at.isoformat()
        )
        for r in rows
    ]


@router.get("/itc-ledger/summary", response_model=ITCLedgerSummaryOut)
def get_itc_ledger_summary(
    registration_id: Optional[str] = None,
    tax_period: Optional[str] = None,
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Returns component-level ledger summary: total availed, reversed permanent, reversed temporary, reavailed, and net balance.
    """
    query = """
    SELECT e.event_type,
           SUM(e.cgst_amount) as cgst,
           SUM(e.sgst_amount) as sgst,
           SUM(e.igst_amount) as igst,
           SUM(e.cess_amount) as cess,
           SUM(e.total_tax) as total,
           SUM(e.interest_amount) as interest,
           COUNT(*) as cnt
    FROM public.itc_ledger_entries e
    JOIN public.purchase_documents p ON e.purchase_doc_id = p.id AND e.org_id = p.org_id
    WHERE e.org_id = :org
    """
    params = {"org": x_org_id}
    if registration_id:
        query += " AND p.registration_id = :reg_id"
        params["reg_id"] = registration_id
    if tax_period:
        query += " AND e.tax_period = :rp"
        params["rp"] = tax_period
    query += " GROUP BY e.event_type;"

    rows = db.execute(text(query), params).fetchall()

    def make_comp():
        return ComponentTaxAmount(cgst=Decimal("0.00"), sgst=Decimal("0.00"), igst=Decimal("0.00"), cess=Decimal("0.00"), total=Decimal("0.00"))

    tot_avail = make_comp()
    tot_rev_perm = make_comp()
    tot_rev_temp = make_comp()
    tot_reav = make_comp()
    tot_interest = Decimal("0.00")
    total_entries = 0

    for r in rows:
        etype = r.event_type
        comp = ComponentTaxAmount(
            cgst=Decimal(str(r.cgst or 0)),
            sgst=Decimal(str(r.sgst or 0)),
            igst=Decimal(str(r.igst or 0)),
            cess=Decimal(str(r.cess or 0)),
            total=Decimal(str(r.total or 0))
        )
        total_entries += r.cnt
        tot_interest += Decimal(str(r.interest or 0))

        if etype == "AVAIL":
            tot_avail.cgst += comp.cgst
            tot_avail.sgst += comp.sgst
            tot_avail.igst += comp.igst
            tot_avail.cess += comp.cess
            tot_avail.total += comp.total
        elif etype in ("REVERSAL_PERMANENT_17_5", "REVERSAL_RULE_38", "REVERSAL_RULE_42_43"):
            tot_rev_perm.cgst += comp.cgst
            tot_rev_perm.sgst += comp.sgst
            tot_rev_perm.igst += comp.igst
            tot_rev_perm.cess += comp.cess
            tot_rev_perm.total += comp.total
        elif etype in ("REVERSAL_RULE_37", "REVERSAL_RULE_37A", "REVERSAL_OTHER_TEMP"):
            tot_rev_temp.cgst += comp.cgst
            tot_rev_temp.sgst += comp.sgst
            tot_rev_temp.igst += comp.igst
            tot_rev_temp.cess += comp.cess
            tot_rev_temp.total += comp.total
        elif etype == "RECLAIM":
            tot_reav.cgst += comp.cgst
            tot_reav.sgst += comp.sgst
            tot_reav.igst += comp.igst
            tot_reav.cess += comp.cess
            tot_reav.total += comp.total

    net_bal = ComponentTaxAmount(
        cgst=tot_avail.cgst - tot_rev_perm.cgst - tot_rev_temp.cgst + tot_reav.cgst,
        sgst=tot_avail.sgst - tot_rev_perm.sgst - tot_rev_temp.sgst + tot_reav.sgst,
        igst=tot_avail.igst - tot_rev_perm.igst - tot_rev_temp.igst + tot_reav.igst,
        cess=tot_avail.cess - tot_rev_perm.cess - tot_rev_temp.cess + tot_reav.cess,
        total=tot_avail.total - tot_rev_perm.total - tot_rev_temp.total + tot_reav.total
    )

    return ITCLedgerSummaryOut(
        total_availed=tot_avail,
        total_reversed_permanent=tot_rev_perm,
        total_reversed_temporary=tot_rev_temp,
        total_reavailed=tot_reav,
        net_active_itc_balance=net_bal,
        total_interest_assessed=tot_interest,
        entries_count=total_entries
    )


@router.get("/ims/history/{ims_action_id}", response_model=List[IMSActionHistoryOut])
def get_ims_action_history(
    ims_action_id: str,
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Returns audit action history for an IMS document record.
    """
    rows = db.execute(
        text("""
        SELECT * FROM public.ims_action_history
        WHERE ims_action_id = :id AND org_id = :org
        ORDER BY created_at ASC;
        """),
        {"id": ims_action_id, "org": x_org_id}
    ).fetchall()
    return [
        IMSActionHistoryOut(
            id=str(r.id),
            ims_action_id=str(r.ims_action_id),
            previous_action=r.previous_action,
            new_action=r.new_action,
            action_reason=r.action_reason,
            actor_id=str(r.actor_id) if r.actor_id else None,
            created_at=r.created_at.isoformat()
        )
        for r in rows
    ]


@router.get("/export/audit-report")
def export_reconciliation_audit_report(
    registration_id: str,
    return_period: str,
    snapshot_id: str,
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Exports a comprehensive reconciliation and ITC ledger audit report with full document provenance.
    """
    t4 = get_gstr3b_table4_summary(
        registration_id=registration_id,
        return_period=return_period,
        snapshot_id=snapshot_id,
        x_org_id=x_org_id,
        db=db
    )
    return {
        "report_type": "ITC_RECONCILIATION_AUDIT_REPORT",
        "return_period": return_period,
        "registration_id": registration_id,
        "generated_at": datetime.utcnow().isoformat(),
        "summary": t4.dict(),
        "unbooked_invoices_count": len(t4.unbooked_gstr2b_documents),
        "unbooked_invoices": t4.unbooked_gstr2b_documents,
        "reversals_lineage_count": len(t4.reversals_lineage),
        "reversals_lineage": t4.reversals_lineage
    }
