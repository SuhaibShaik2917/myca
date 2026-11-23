import os
import pytest
from decimal import Decimal
import uuid
import psycopg
from app.database import SessionLocal, set_tenant_context
from app.modules.gstr3b.schemas import TaxBreakdown
from app.modules.gstr3b.offset_engine import compute_statutory_tax_offset
from app.modules.gstr3b.gsp_sandbox import simulate_gsp_gstn_filing, GSPSandboxFilingError, generate_arn
from app.modules.gstr3b.service import (
    prepare_gstr3b_return, submit_gstr3b_for_review, review_gstr3b_decision,
    file_gstr3b_to_gsp_sandbox, get_gstr3b_status, GSTR3BServiceError
)

from tests.test_db_config import DB_ADMIN

import random
import string

@pytest.fixture
def m3_tenant():
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            user_id = str(uuid.uuid4())
            rand_prefix = "".join(random.choices(string.ascii_uppercase, k=5))
            rand_digits = f"{random.randint(1000, 9999)}"
            pan = f"{rand_prefix}{rand_digits}Z"
            gstin = f"27{pan}1ZT"
            reg_id = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'M3 Test Org', %s);", (org_id, pan))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'M3 Tester');", (user_id, f"m3_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_id, user_id))
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'M3 Legal Entity', true);
            """, (reg_id, org_id, gstin))

    return {"org_id": org_id, "user_id": user_id, "reg_id": reg_id, "gstin": gstin}

def test_rule_88a_igst_credit_priority_and_exhaustion():
    liab = TaxBreakdown(
        igst=Decimal("10000.00"),
        cgst=Decimal("5000.00"),
        sgst=Decimal("5000.00"),
        cess=Decimal("0.00"),
        total=Decimal("20000.00")
    )
    credit = TaxBreakdown(
        igst=Decimal("16000.00"),
        cgst=Decimal("4000.00"),
        sgst=Decimal("4000.00"),
        cess=Decimal("0.00"),
        total=Decimal("24000.00")
    )
    res = compute_statutory_tax_offset(liab, credit)

    assert res.igst.paid_by_igst_credit == Decimal("10000.00")
    assert res.igst.remaining_liability == Decimal("0.00")
    assert res.cgst.paid_by_igst_credit == Decimal("3000.00")
    assert res.sgst.paid_by_igst_credit == Decimal("3000.00")
    assert res.cgst.paid_by_cgst_credit == Decimal("2000.00")
    assert res.sgst.paid_by_sgst_credit == Decimal("2000.00")
    assert res.total_challan_required == Decimal("0.00")
    assert res.remaining_credit_balance.cgst == Decimal("2000.00")
    assert res.remaining_credit_balance.sgst == Decimal("2000.00")

def test_cgst_sgst_cross_offset_strictly_prohibited():
    liab = TaxBreakdown(
        igst=Decimal("0.00"),
        cgst=Decimal("0.00"),
        sgst=Decimal("10000.00"),
        cess=Decimal("0.00"),
        total=Decimal("10000.00")
    )
    credit = TaxBreakdown(
        igst=Decimal("0.00"),
        cgst=Decimal("50000.00"),
        sgst=Decimal("0.00"),
        cess=Decimal("0.00"),
        total=Decimal("50000.00")
    )
    res = compute_statutory_tax_offset(liab, credit)

    assert res.sgst.paid_by_cgst_credit == Decimal("0.00")
    assert res.sgst.total_credit_used == Decimal("0.00")
    assert res.sgst.remaining_liability == Decimal("10000.00")
    assert res.sgst.challan_required == Decimal("10000.00")
    assert res.total_challan_required == Decimal("10000.00")

def test_cash_challan_pmt06_exact_calculation():
    liab = TaxBreakdown(
        igst=Decimal("25000.00"),
        cgst=Decimal("12000.00"),
        sgst=Decimal("12000.00"),
        cess=Decimal("5000.00"),
        total=Decimal("54000.00")
    )
    credit = TaxBreakdown(
        igst=Decimal("5000.00"),
        cgst=Decimal("2000.00"),
        sgst=Decimal("2000.00"),
        cess=Decimal("1000.00"),
        total=Decimal("10000.00")
    )
    res = compute_statutory_tax_offset(liab, credit)

    assert res.total_tax_paid_by_credit == Decimal("10000.00")
    assert res.total_challan_required == Decimal("44000.00")
    assert res.igst.challan_required == Decimal("20000.00")
    assert res.cgst.challan_required == Decimal("10000.00")
    assert res.sgst.challan_required == Decimal("10000.00")
    assert res.cess.challan_required == Decimal("4000.00")

def test_gsp_sandbox_arn_syntax_and_idempotency():
    arn = generate_arn("27", "2026-11", sequence_id=42)
    assert arn.startswith("AA271126")
    assert len(arn) == 16

def test_maker_checker_workflow_and_filing_gate(m3_tenant):
    t = m3_tenant
    db = SessionLocal()
    tax_period = "2026-11"
    try:
        set_tenant_context(db, org_id=t["org_id"], user_id=t["user_id"])

        # 1. Prepare GSTR-3B Return
        prep = prepare_gstr3b_return(
            session=db,
            org_id=t["org_id"],
            registration_id=t["reg_id"],
            tax_period=tax_period,
            user_id=t["user_id"]
        )
        filing_id = prep["id"]
        assert prep["filing_status"] == "DRAFT"

        # 2. Gate: Attempting to file while in DRAFT must fail
        with pytest.raises(GSTR3BServiceError, match="Filing Gate Blocked"):
            file_gstr3b_to_gsp_sandbox(db, t["org_id"], filing_id, t["user_id"])

        # 3. Maker submits for review
        sub = submit_gstr3b_for_review(db, t["org_id"], filing_id, t["user_id"], "Please review Table 4 reversals.")
        assert sub["status"] == "SUBMITTED_FOR_REVIEW"

        # 4. Checker rejects with notes
        rej = review_gstr3b_decision(db, t["org_id"], filing_id, str(uuid.uuid4()), "REJECTED", "Verify RCM challan numbers.")
        assert rej["status"] == "REJECTED_BY_CHECKER"

        # 5. Maker resubmits
        sub2 = submit_gstr3b_for_review(db, t["org_id"], filing_id, t["user_id"], "RCM verified. Resubmitted.")
        assert sub2["status"] == "SUBMITTED_FOR_REVIEW"

        # 6. Checker approves
        checker_user = str(uuid.uuid4())
        app = review_gstr3b_decision(db, t["org_id"], filing_id, checker_user, "APPROVED", "Statutory checks verified. Approved to file.")
        assert app["status"] == "APPROVED_BY_CHECKER"

        # 7. File to GSP Sandbox
        filing_res = file_gstr3b_to_gsp_sandbox(db, t["org_id"], filing_id, t["user_id"])
        assert filing_res.status == "FILED"
        assert filing_res.arn.startswith("AA27")
        assert filing_res.cin is not None
        assert filing_res.payload_hash is not None

        # 8. Check persistence
        status = get_gstr3b_status(db, t["org_id"], t["reg_id"], tax_period)
        assert status["filing_status"] == "FILED"
        assert status["arn"] == filing_res.arn

        # 9. Gate: Attempting to file again after FILED must be blocked
        with pytest.raises(GSTR3BServiceError, match="Filing Gate Blocked"):
            file_gstr3b_to_gsp_sandbox(db, t["org_id"], filing_id, t["user_id"])

    finally:
        db.close()
