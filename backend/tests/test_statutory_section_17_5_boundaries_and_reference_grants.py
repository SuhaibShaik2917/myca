"""
Regression & Statutory Verification Test Suite:
1. Least-Privilege Reference Catalogue Write Denial under app_service_user
2. Section 17(5)(a) and Section 17(5)(ab) Vehicle Seating Threshold (13 vs 14 seats)
3. Goods Transport Vehicles, Passenger Transport Permits, and Ambiguous Facts
4. Section 17(5)(ab) Vehicle Repairs, Driving Schools, Manufacturers & Insurers
"""
import uuid
import pytest
from decimal import Decimal
from sqlalchemy import text
from app.database import SessionLocal, set_tenant_context
from app.modules.reconciliation.itc_engine import evaluate_section_17_5_vehicle_and_repair_eligibility


# ============================================================================
# 1. LEAST-PRIVILEGE DATABASE GRANTS ON REFERENCE CATALOGUES
# ============================================================================

def test_app_service_user_cannot_write_to_reference_catalogues():
    """
    Finding 1: Proves that app_service_user has strictly SELECT privilege on
    public reference catalogues (government_schemes, scheme_eligibility_rules),
    and all INSERT, UPDATE, DELETE attempts on BOTH tables are rejected at the database level.
    Also explicitly verifies that the connection is running as 'app_service_user'.
    """
    db = SessionLocal()
    try:
        # 0. Explicitly verify current_user is app_service_user
        current_db_user = db.execute(text("SELECT current_user;")).scalar()
        assert current_db_user == "app_service_user", (
            f"Expected database connection role 'app_service_user', got '{current_db_user}'"
        )

        # A. SELECT is permitted on both tables
        schemes = db.execute(text("SELECT scheme_code, name FROM public.government_schemes LIMIT 5;")).fetchall()
        assert len(schemes) >= 1, "Expected to read from government_schemes reference catalogue"
        rules = db.execute(text("SELECT id, scheme_id FROM public.scheme_eligibility_rules LIMIT 5;")).fetchall()
        assert len(rules) >= 1, "Expected to read from scheme_eligibility_rules reference catalogue"

        # B. INSERT on government_schemes is DENIED
        insert_schemes_blocked = False
        try:
            db.execute(text("""
                INSERT INTO public.government_schemes (scheme_code, name, ministry, category, description, benefits_summary)
                VALUES ('HACK_SCHEME', 'Malicious Scheme', 'Test', 'credit_guarantee', 'Test write', 'None');
            """))
            db.commit()
        except Exception as e:
            insert_schemes_blocked = True
            db.rollback()
            assert "permission denied" in str(e).lower() or "insufficientprivilege" in str(e).lower() or "programmingerror" in str(e).lower()

        assert insert_schemes_blocked, "CRITICAL SECURITY BREACH: app_service_user was able to INSERT into government_schemes!"

        # C. UPDATE on government_schemes is DENIED
        update_schemes_blocked = False
        try:
            db.execute(text("UPDATE public.government_schemes SET name = 'Hacked Name' WHERE scheme_code = 'CGTMSE';"))
            db.commit()
        except Exception as e:
            update_schemes_blocked = True
            db.rollback()
            assert "permission denied" in str(e).lower() or "insufficientprivilege" in str(e).lower() or "programmingerror" in str(e).lower()

        assert update_schemes_blocked, "CRITICAL SECURITY BREACH: app_service_user was able to UPDATE government_schemes!"

        # D. DELETE on government_schemes is DENIED
        delete_schemes_blocked = False
        try:
            db.execute(text("DELETE FROM public.government_schemes WHERE scheme_code = 'CGTMSE';"))
            db.commit()
        except Exception as e:
            delete_schemes_blocked = True
            db.rollback()
            assert "permission denied" in str(e).lower() or "insufficientprivilege" in str(e).lower() or "programmingerror" in str(e).lower()

        assert delete_schemes_blocked, "CRITICAL SECURITY BREACH: app_service_user was able to DELETE from government_schemes!"

        # E. INSERT on scheme_eligibility_rules is DENIED
        insert_rules_blocked = False
        try:
            db.execute(text("""
                INSERT INTO public.scheme_eligibility_rules (id, scheme_id, rule_key, operator, target_value, rule_description)
                VALUES ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002', 'credit_score', '>=', '500', 'Hack Rule');
            """))
            db.commit()
        except Exception as e:
            insert_rules_blocked = True
            db.rollback()
            assert "permission denied" in str(e).lower() or "insufficientprivilege" in str(e).lower() or "programmingerror" in str(e).lower()

        assert insert_rules_blocked, "CRITICAL SECURITY BREACH: app_service_user was able to INSERT into scheme_eligibility_rules!"

        # F. UPDATE on scheme_eligibility_rules is DENIED
        update_rules_blocked = False
        try:
            db.execute(text("UPDATE public.scheme_eligibility_rules SET target_value = '999' WHERE rule_key = 'credit_score';"))
            db.commit()
        except Exception as e:
            update_rules_blocked = True
            db.rollback()
            assert "permission denied" in str(e).lower() or "insufficientprivilege" in str(e).lower() or "programmingerror" in str(e).lower()

        assert update_rules_blocked, "CRITICAL SECURITY BREACH: app_service_user was able to UPDATE scheme_eligibility_rules!"

        # G. DELETE on scheme_eligibility_rules is DENIED
        delete_rules_blocked = False
        try:
            db.execute(text("DELETE FROM public.scheme_eligibility_rules WHERE rule_key = 'credit_score';"))
            db.commit()
        except Exception as e:
            delete_rules_blocked = True
            db.rollback()
            assert "permission denied" in str(e).lower() or "insufficientprivilege" in str(e).lower() or "programmingerror" in str(e).lower()

        assert delete_rules_blocked, "CRITICAL SECURITY BREACH: app_service_user was able to DELETE from scheme_eligibility_rules!"

    finally:
        db.close()


# ============================================================================
# 2. SECTION 17(5)(ab) VEHICLE SEATING CAPACITY BOUNDARY: 13 VS 14 SEATS
# ============================================================================

def test_section_17_5_exact_13_seats_vs_14_seats_boundary():
    """
    Finding 2: Evaluates Section 17(5)(a) and 17(5)(ab) seating capacity boundary:
    - Seating capacity of NOT MORE THAN 13 persons (<= 13) is BLOCKED.
    - Seating capacity of MORE THAN 13 persons (> 13, i.e. 14+) is ELIGIBLE.
    """
    # 1. Exactly 13 seats (e.g. 13-seater tempo traveller without passenger transport permit)
    res_13 = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Tempo Traveller 13-seater",
        hsn_sac="8702",
        facts={"seating_capacity": 13, "is_factual_context_verified": True}
    )
    assert res_13["applies"] is True
    assert res_13["is_blocked"] is True
    assert res_13["status"] == "BLOCKED_CREDIT_CONFIRMED"
    assert "NOT MORE THAN 13 persons" in res_13["statutory_citation"]

    # 2. Exactly 14 seats (e.g. 14-seater minibus)
    res_14 = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Minibus 14-seater",
        hsn_sac="8702",
        facts={"seating_capacity": 14, "is_factual_context_verified": True}
    )
    assert res_14["applies"] is True
    assert res_14["is_blocked"] is False
    assert res_14["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"
    assert "MORE THAN 13 persons" in res_14["statutory_citation"]

    # 3. High capacity vehicles (25-seater bus, 50-seater coach)
    res_25 = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Staff Bus 25-seater",
        hsn_sac="8702",
        facts={"seating_capacity": 25, "is_factual_context_verified": True}
    )
    assert res_25["is_blocked"] is False
    assert res_25["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"

    # 4. Small motor car (5-seater sedan)
    res_5 = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Sedan Car",
        hsn_sac="8703",
        facts={"seating_capacity": 5, "is_factual_context_verified": True}
    )
    assert res_5["is_blocked"] is True
    assert res_5["status"] == "BLOCKED_CREDIT_CONFIRMED"


# ============================================================================
# 3. GOODS VEHICLES, COMMERCIAL PERMITS & AMBIGUOUS FACTS
# ============================================================================

def test_section_17_5_goods_transport_and_statutory_facts():
    """
    Tests independent statutory facts:
    - Goods transport vehicle (HSN 8704 / trucks)
    - Dual requirement for Section 17(5)(a)(B): Permit ALONE is insufficient; requires BOTH
      permit AND actual use for taxable passenger transport supplies.
    - Permit alone without actual use evidence -> REVIEW_REQUIRED_FACTS_PENDING
    - Actual use claimed without permit -> REVIEW_REQUIRED_FACTS_PENDING
    - Missing or unverified vehicle facts (routes to REVIEW_REQUIRED_FACTS_PENDING)
    - Missing seating capacity when facts verified -> REVIEW_REQUIRED_FACTS_PENDING
    - Empty or None facts dictionary -> REVIEW_REQUIRED_FACTS_PENDING
    """
    # 1. Goods transport vehicle (HSN 8704 delivery truck) - verified
    res_truck = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Delivery Truck",
        hsn_sac="8704",
        facts={"is_goods_transport_vehicle": True, "is_factual_context_verified": True}
    )
    assert res_truck["is_blocked"] is False
    assert res_truck["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"
    assert "Goods transport vehicle" in res_truck["statutory_citation"]

    # 2. Goods transport vehicle - unverified RC
    res_truck_unverified = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Delivery Truck",
        hsn_sac="8704",
        facts={"is_goods_transport_vehicle": True, "is_factual_context_verified": False}
    )
    assert res_truck_unverified["is_blocked"] is None
    assert res_truck_unverified["status"] == "REVIEW_REQUIRED_FACTS_PENDING"

    # 3. 13-seater vehicle WITH permit ALONE (actual taxable use NOT established)
    # Under Section 17(5)(a)(B), permit alone does NOT grant exemption
    res_13_permit_only = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Commercial Taxi 13-seater",
        hsn_sac="8702",
        facts={
            "seating_capacity": 13,
            "is_factual_context_verified": True,
            "passenger_transport_license": True,
            "used_for_taxable_passenger_transport": False
        }
    )
    assert res_13_permit_only["is_blocked"] is None
    assert res_13_permit_only["status"] == "REVIEW_REQUIRED_FACTS_PENDING"
    assert "Commercial permit present, but actual use" in res_13_permit_only["statutory_citation"]

    # 4. 13-seater vehicle WITH BOTH verified commercial permit AND actual taxable passenger transport use
    res_13_dual_verified = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Commercial Taxi 13-seater",
        hsn_sac="8702",
        facts={
            "seating_capacity": 13,
            "is_factual_context_verified": True,
            "passenger_transport_license": True,
            "used_for_taxable_passenger_transport": True
        }
    )
    assert res_13_dual_verified["is_blocked"] is False
    assert res_13_dual_verified["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"
    assert "Section 17(5)(a)(B) exception" in res_13_dual_verified["statutory_citation"]

    # 5. Taxpayer claims passenger transport business, but NO license/permit is verified
    res_claim_unsupported = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Taxi Service Expense",
        hsn_sac="8703",
        facts={
            "seating_capacity": 5,
            "is_factual_context_verified": True,
            "is_passenger_transport_business": True,
            "passenger_transport_license": False
        }
    )
    assert res_claim_unsupported["status"] == "REVIEW_REQUIRED_FACTS_PENDING"
    assert res_claim_unsupported["is_blocked"] is None

    # 6. Verified facts but seating capacity is MISSING (seating_capacity: None)
    res_missing_seats = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Passenger Van",
        hsn_sac="8702",
        facts={
            "is_factual_context_verified": True,
            "seating_capacity": None
        }
    )
    assert res_missing_seats["status"] == "REVIEW_REQUIRED_FACTS_PENDING"
    assert res_missing_seats["is_blocked"] is None
    assert "Approved seating capacity missing" in res_missing_seats["statutory_citation"]

    # 7. Bare invoice with no facts provided at all (facts=None)
    res_bare = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Vehicle Purchase",
        hsn_sac="8703",
        facts=None
    )
    assert res_bare["status"] == "REVIEW_REQUIRED_FACTS_PENDING"
    assert res_bare["is_blocked"] is None


# ============================================================================
# 4. SECTION 17(5)(ab) VEHICLE REPAIRS, SERVICING & PROVISO (ii)
# ============================================================================

def test_section_17_5_ab_vehicle_repair_lineage():
    """
    Tests Section 17(5)(ab) repair services:
    - Repair of 14-seater vehicle: Eligible
    - Repair of 13-seater vehicle: Blocked
    - Repair of vehicle used in driving school: Requires dual evidence (license + actual training use)
    - Proviso (ii): Vehicle Manufacturer receiving repair/maintenance services -> Eligible
    - Proviso (ii): General Insurer receiving repair/maintenance services -> Eligible
    """
    # 1. Servicing of 14-seater vehicle (SAC 998729)
    res_repair_14 = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Bus Periodic Maintenance",
        hsn_sac="998729",
        facts={"seating_capacity": 14, "is_factual_context_verified": True}
    )
    assert res_repair_14["is_blocked"] is False
    assert res_repair_14["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"

    # 2. Servicing of 13-seater vehicle (SAC 998729) without statutory exception
    res_repair_13 = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Van Periodic Servicing",
        hsn_sac="998729",
        facts={"seating_capacity": 13, "is_factual_context_verified": True}
    )
    assert res_repair_13["is_blocked"] is True
    assert res_repair_13["status"] == "BLOCKED_CREDIT_CONFIRMED"

    # 3. Repair of driving school car (Section 17(5)(a)(C)) - dual verified
    res_school_repair = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Driving School Car Clutch Overhaul",
        hsn_sac="998729",
        facts={
            "seating_capacity": 5,
            "is_factual_context_verified": True,
            "driving_school_license": True,
            "used_for_driving_training": True
        }
    )
    assert res_school_repair["is_blocked"] is False
    assert res_school_repair["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"
    assert "Section 17(5)(a)(C)" in res_school_repair["statutory_citation"]

    # 4. Repair of driving school car - license missing or unverified
    res_school_unverified = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Driving School Car Clutch Overhaul",
        hsn_sac="998729",
        facts={
            "seating_capacity": 5,
            "is_factual_context_verified": True,
            "is_driving_school_training": True,
            "driving_school_license": False
        }
    )
    assert res_school_unverified["is_blocked"] is None
    assert res_school_unverified["status"] == "REVIEW_REQUIRED_FACTS_PENDING"

    # 5. Section 17(5)(ab) Proviso (ii)(I): Motor vehicle manufacturer receiving repairs - positive (verified)
    res_mfr = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Factory Vehicle Test Track Repair",
        hsn_sac="998729",
        facts={
            "is_vehicle_manufacturer": True,
            "manufacturing_license": True,
            "relates_to_manufactured_vehicles": True,
            "is_factual_context_verified": True
        }
    )
    assert res_mfr["is_blocked"] is False
    assert res_mfr["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"
    assert "Section 17(5)(ab) Proviso (ii)(I)" in res_mfr["statutory_citation"]

    # 5b. Manufacturer negative: license unverified
    res_mfr_unverified_lic = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Factory Vehicle Repair",
        hsn_sac="998729",
        facts={
            "is_vehicle_manufacturer": True,
            "manufacturing_license": False,
            "is_factual_context_verified": True
        }
    )
    assert res_mfr_unverified_lic["is_blocked"] is None
    assert res_mfr_unverified_lic["status"] == "REVIEW_REQUIRED_FACTS_PENDING"

    # 5c. Manufacturer negative: factual context unverified
    res_mfr_unverified_context = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Factory Vehicle Repair",
        hsn_sac="998729",
        facts={
            "is_vehicle_manufacturer": True,
            "manufacturing_license": True,
            "is_factual_context_verified": False
        }
    )
    assert res_mfr_unverified_context["is_blocked"] is None
    assert res_mfr_unverified_context["status"] == "REVIEW_REQUIRED_FACTS_PENDING"

    # 6. Section 17(5)(ab) Proviso (ii)(II): General insurer receiving repairs - positive (verified + insured)
    res_ins = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Accident Claim Settlement Repair",
        hsn_sac="998729",
        facts={
            "is_general_insurer": True,
            "general_insurance_license": True,
            "is_vehicle_insured_by_taxpayer": True,
            "is_factual_context_verified": True
        }
    )
    assert res_ins["is_blocked"] is False
    assert res_ins["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"
    assert "Section 17(5)(ab) Proviso (ii)(II)" in res_ins["statutory_citation"]

    # 6b. Insurer negative: vehicle is NOT insured by taxpayer (missing policy evidence)
    res_ins_not_insured = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Vehicle Repair Expense",
        hsn_sac="998729",
        facts={
            "is_general_insurer": True,
            "general_insurance_license": True,
            "is_vehicle_insured_by_taxpayer": False,
            "is_factual_context_verified": True
        }
    )
    assert res_ins_not_insured["is_blocked"] is None
    assert res_ins_not_insured["status"] == "REVIEW_REQUIRED_FACTS_PENDING"
    assert "evidence that the motor vehicle is insured by the taxpayer is missing" in res_ins_not_insured["statutory_citation"]

    # 6c. Insurer negative: IRDAI license unverified
    res_ins_no_lic = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Vehicle Repair Expense",
        hsn_sac="998729",
        facts={
            "is_general_insurer": True,
            "general_insurance_license": False,
            "is_vehicle_insured_by_taxpayer": True,
            "is_factual_context_verified": True
        }
    )
    assert res_ins_no_lic["is_blocked"] is None
    assert res_ins_no_lic["status"] == "REVIEW_REQUIRED_FACTS_PENDING"

    # 6d. Insurer negative: context unverified
    res_ins_unverified = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Accident Claim Settlement Repair",
        hsn_sac="998729",
        facts={
            "is_general_insurer": True,
            "general_insurance_license": True,
            "is_vehicle_insured_by_taxpayer": True,
            "is_factual_context_verified": False
        }
    )
    assert res_ins_unverified["is_blocked"] is None
    assert res_ins_unverified["status"] == "REVIEW_REQUIRED_FACTS_PENDING"


# ============================================================================
# 5. DOWNSTREAM REVIEW STATUS PRESERVATION IN ENGINE & GSTR-3B FLOW
# ============================================================================

def test_review_required_preservation_in_itc_engine_and_gstr3b():
    """
    Finding B: Proves that:
    1. An unverified/facts-pending assessment receives status REVIEW_REQUIRED (not BLOCKED_CREDIT_INDICATOR).
    2. Its risk_reversal_amount is 0.00 (not confirmed statutory reversal).
    3. GSTR-3B preparation does NOT reverse REVIEW_REQUIRED items in Table 4(B)(1).
    """
    from datetime import date
    from app.modules.reconciliation.itc_engine import StatutoryITCRiskEngine
    from app.modules.reconciliation.schemas import ITCEligibilityEnum
    from app.modules.reconciliation.gstr3b_itc import generate_gstr3b_table4_summary

    engine = StatutoryITCRiskEngine()
    today = date(2026, 11, 23)

    # 1. Vehicle invoice with unverified facts (context pending)
    p_unverified_veh = {
        "id": "p_unverified_veh_1",
        "document_number": "INV-VEH-PENDING",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("14000.00"),
        "sgst_amount": Decimal("14000.00"),
        "igst_amount": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "total_invoice_value": Decimal("180000.00"),
        "goods_receipt_date": "2026-11-02",
        "goods_receipt_note_ref": "GRN-VEH",
        "item_description": "Vehicle Purchase",
        "hsn_sac_code": "8703",
        "facts": {"is_factual_context_verified": False}  # Unverified!
    }

    g_doc = {
        "id": "g_doc_1",
        "section_name": "B2B",
        "itc_availability": "available",
        "central_tax": Decimal("14000.00"),
        "state_ut_tax": Decimal("14000.00"),
        "integrated_tax": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "document_number": "INV-VEH-PENDING",
        "document_date": "2026-11-01"
    }

    evals = engine.evaluate_itc_eligibility(
        purchase_doc=p_unverified_veh,
        gstr2b_doc=g_doc,
        evaluation_date=today
    )

    veh_eval = next(e for e in evals if e.rule_code == "SEC_17_5_MOTOR")
    # Must preserve REVIEW_REQUIRED status throughout the flow
    assert veh_eval.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED, (
        f"Expected REVIEW_REQUIRED, got {veh_eval.eligibility_status}"
    )
    # Must NOT present as a confirmed statutory reversal
    assert veh_eval.risk_reversal_amount == Decimal("0.00"), (
        f"Expected risk_reversal_amount=0.00 for pending review, got {veh_eval.risk_reversal_amount}"
    )
    # Potential ITC is preserved for visibility
    assert veh_eval.potential_itc == Decimal("28000.00")

    # 2. Downstream GSTR-3B Table 4 Preparation
    t4 = generate_gstr3b_table4_summary(
        return_period="112026",
        recipient_gstin="27AAAAA0000A1Z5",
        gstr2b_docs=[g_doc],
        purchase_docs=[p_unverified_veh],
        matches=[],
        assessments=[veh_eval.model_dump()]
    )

    # In GSTR-3B Table 4(B)(1), permanent reversals must be 0 (item is pending review, not confirmed blocked)
    assert t4.itc_reversed_rule_38_42_43 == Decimal("0.00")
    assert t4.ineligible_sec_17_5_blocked == Decimal("0.00")
    assert t4.total_itc_reversed == Decimal("0.00")

    # Net ITC in Table 4(C) retains the full amount until CA confirms block
    assert t4.net_itc_available == Decimal("28000.00")
