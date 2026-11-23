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

    # 5d. Manufacturer negative: nexus missing or unverified
    res_mfr_unverified_nexus = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="General Vehicle Repair",
        hsn_sac="998729",
        facts={
            "is_vehicle_manufacturer": True,
            "manufacturing_license": True,
            "relates_to_manufactured_vehicles": False,
            "is_factual_context_verified": True
        }
    )
    assert res_mfr_unverified_nexus["is_blocked"] is None
    assert res_mfr_unverified_nexus["status"] == "REVIEW_REQUIRED_FACTS_PENDING"
    assert "Services do not relate to vehicles manufactured by the taxpayer" in res_mfr_unverified_nexus["statutory_citation"]

    # 5e. Manufacturer negative: nexus omitted entirely from facts
    res_mfr_omitted_nexus = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Factory Vehicle Repair",
        hsn_sac="998729",
        facts={
            "is_vehicle_manufacturer": True,
            "manufacturing_license": True,
            "is_factual_context_verified": True
            # relates_to_manufactured_vehicles omitted
        }
    )
    assert res_mfr_omitted_nexus["is_blocked"] is None
    assert res_mfr_omitted_nexus["status"] == "REVIEW_REQUIRED_FACTS_PENDING"
    assert "Services do not relate to vehicles manufactured by the taxpayer" in res_mfr_omitted_nexus["statutory_citation"]

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

    # 6e. Commercial business negative: having car repaired with insurance policy attached
    # Must NOT be falsely routed to missing IRDAI license; must evaluate seating capacity
    res_commercial_with_policy = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Executive Car Repair",
        hsn_sac="998729",
        facts={
            "is_factual_context_verified": True,
            "policy_number": "POL-COMM-9988",
            "seating_capacity": 5
        }
    )
    assert res_commercial_with_policy["is_blocked"] is True
    assert res_commercial_with_policy["status"] == "BLOCKED_CREDIT_CONFIRMED"
    assert "NOT MORE THAN 13 persons" in res_commercial_with_policy["statutory_citation"]

    # 6f. Insurer negative: Valid insurer with verified policy document that does NOT establish vehicle was insured by this taxpayer
    res_ins_third_party_policy = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Vehicle Repair Expense",
        hsn_sac="998729",
        facts={
            "is_general_insurer": True,
            "general_insurance_license": True,
            "insurance_policy_verified": True,
            "policy_issuer_is_taxpayer": False,
            "is_factual_context_verified": True
        }
    )
    assert res_ins_third_party_policy["is_blocked"] is None
    assert res_ins_third_party_policy["status"] == "REVIEW_REQUIRED_FACTS_PENDING"
    assert "evidence that the motor vehicle is insured by the taxpayer is missing" in res_ins_third_party_policy["statutory_citation"]

    # 6g. Insurer negative: Generic policy document verified alone without confirming vehicle is insured by taxpayer
    res_ins_policy_only = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Vehicle Repair Expense",
        hsn_sac="998729",
        facts={
            "is_general_insurer": True,
            "general_insurance_license": True,
            "insurance_policy_verified": True,
            "is_factual_context_verified": True
        }
    )
    assert res_ins_policy_only["is_blocked"] is None
    assert res_ins_policy_only["status"] == "REVIEW_REQUIRED_FACTS_PENDING"
    assert "evidence that the motor vehicle is insured by the taxpayer is missing" in res_ins_policy_only["statutory_citation"]


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
    from app.modules.reconciliation.itc_engine import (
        StatutoryITCRiskEngine,
        generate_auditor_verification_signature,
    )
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


def test_multi_line_invoice_exception_and_blocked_catering():
    """
    Comprehensive Statutory Validation for Multi-Line Mixed Invoices:
    - Line 1: Vehicle repair qualifying for Section 17(5)(ab) manufacturer exception (is_blocked = False).
    - Line 2: Food & catering service (HSN 9963) blocked under Section 17(5)(b).

    Verifies:
    1. Scenario A: Explicit Line-Level Tax Allocation:
       - Exact line components (₹2,000 catering reversed out of ₹5,000 total invoice tax).
    2. Scenario B1: Rate-Aware Proportional Allocation:
       - Vehicle repair taxed at 18% (₹20,000 taxable -> ₹3,600 tax).
       - Catering dinner taxed at 5% (₹10,000 taxable -> ₹500 tax).
       - Total invoice tax = ₹4,100.
       - Confirms catering reversal is ₹500.00, NOT ₹1,366.67 from an unweighted taxable value ratio.
    3. Scenario B2: Explicit Line Tax Discrepancy Gate:
       - Line taxes (₹3,000 + ₹1,500) fail to reconcile with invoice tax (₹5,000).
       - Routes to REVIEW_REQUIRED with risk_reversal_amount = Decimal("0.00") for CA review.
    4. Scenario C1: Missing Applicable GST Rates:
       - Mixed statutory lines without rate specifications cannot be assumed equal;
         routes to REVIEW_REQUIRED with risk_reversal_amount = Decimal("0.00").
    5. Scenario C2: Disallowed GST-Inclusive Total Amount Basis:
       - Only total_amount/line_total provided without pure taxable_value;
         routes to REVIEW_REQUIRED with risk_reversal_amount = Decimal("0.00").
    6. Scenario C3: Completely Missing Line Breakdown:
       - Missing line-level tax and taxable values route to REVIEW_REQUIRED with 0.00 reversal.
    7. Scenario D1: Document-Level Rate without Verified Uniformity Gate:
       - Multi-line invoice cannot inherit document rate without is_uniform_rate_verified.
    8. Scenario D2: Non-Statutory Rate on Line Items:
       - Blended / arbitrary rate (e.g. 13.67%) rejected as invalid statutory rate.
    9. Scenario D3: Document-Level Rate WITH Verified Uniformity Gate:
       - is_uniform_rate_verified allows valid statutory rate across lines.
    10. Scenario D4: Dynamic Rounding Tolerance (N * 0.01) Exceeded:
        - Discrepancy exceeding tolerance routes to REVIEW_REQUIRED with 0.00 reversal.
    11. Scenario D5: Legitimate 40.00% Statutory Rate:
        - Notification No. 9/2025-Integrated Tax (Rate) 40% rate accepted on eligible items.
    12. Scenario D6: Legitimate 0.25% Statutory Rate:
        - Schedule V rate (0.25%) parsed accurately without multiplying by 100 to 25.00%.
    13. Scenario D7: Rejection of Unauthenticated Booleans, String Traps, and Mismatched HSN Rates:
        - String values ("false", "0", "true") and raw unauthenticated booleans rejected.
        - Mismatched HSN rates (e.g. 40% on catering 9963, 40% on biris 24031921, 40% on general 2106) rejected.
    14. Scenario D8: Structured Verification Record Acceptance:
        - Audit metadata with authorized role, verifier identity, evidence ref, and authorization token accepted.
    15. Scenario D9: Explicit Rate Representation Contract:
        - Percentage points primary (e.g. 0.25%, 18.00%); dedicated rate_fraction accepted; ambiguous 0.18 rejected.
    """
    from datetime import date
    from app.modules.reconciliation.itc_engine import (
        StatutoryITCRiskEngine,
        generate_auditor_verification_signature,
    )
    from app.modules.reconciliation.schemas import ITCEligibilityEnum

    engine = StatutoryITCRiskEngine()
    today = date(2026, 11, 23)

    # 1. Scenario A: Explicit Line-Level Tax Allocation (Reconciled)
    p_doc_explicit = {
        "id": "p_multi_line_explicit",
        "document_number": "INV-MULTI-001",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2500.00"),
        "sgst_amount": Decimal("2500.00"),
        "igst_amount": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "total_invoice_value": Decimal("30000.00"),
        "goods_receipt_date": "2026-11-02",
        "goods_receipt_note_ref": "GRN-MULTI",
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "tax_amount": Decimal("3000.00"),
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "tax_amount": Decimal("2000.00")
            }
        ]
    }

    g_doc_explicit = {
        "id": "g_multi_1",
        "section_name": "B2B",
        "itc_availability": "available",
        "central_tax": Decimal("2500.00"),
        "state_ut_tax": Decimal("2500.00"),
        "integrated_tax": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "document_number": "INV-MULTI-001",
        "document_date": "2026-11-01"
    }

    evals_explicit = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_explicit,
        gstr2b_doc=g_doc_explicit,
        evaluation_date=today
    )

    food_eval = next(e for e in evals_explicit if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval.eligibility_status == ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR
    # Reversal is exactly the line tax of ₹2,000, preserving ₹3,000 eligible repair credit
    assert food_eval.risk_reversal_amount == Decimal("2000.00")
    assert food_eval.potential_itc == Decimal("2000.00")

    # 2. Scenario B1: Rate-Aware Proportional Tax Allocation (Mixed 18% and 5% Rates)
    p_doc_rate_aware = {
        "id": "p_multi_line_rate_aware",
        "document_number": "INV-MULTI-002",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2050.00"),
        "sgst_amount": Decimal("2050.00"),
        "igst_amount": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "total_invoice_value": Decimal("34100.00"),
        "goods_receipt_date": "2026-11-02",
        "goods_receipt_note_ref": "GRN-MULTI-2",
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "gst_rate": Decimal("18.00"),
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "taxable_value": Decimal("10000.00"),
                "gst_rate": Decimal("5.00")
            }
        ]
    }

    g_doc_rate_aware = {
        "id": "g_multi_2",
        "section_name": "B2B",
        "itc_availability": "available",
        "central_tax": Decimal("2050.00"),
        "state_ut_tax": Decimal("2050.00"),
        "integrated_tax": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "document_number": "INV-MULTI-002",
        "document_date": "2026-11-01"
    }

    evals_rate = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_rate_aware,
        gstr2b_doc=g_doc_rate_aware,
        evaluation_date=today
    )

    food_eval_rate = next(e for e in evals_rate if e.rule_code == "SEC_17_5_FOOD")
    # Rate-aware calculation: ₹10,000 @ 5% = ₹500.00 (NOT ₹1,366.67 from an unweighted ratio!)
    assert food_eval_rate.eligibility_status == ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR
    assert food_eval_rate.risk_reversal_amount == Decimal("500.00")
    assert food_eval_rate.potential_itc == Decimal("500.00")

    # 3. Scenario B2: Explicit Line Tax Discrepancy Gate
    p_doc_mismatch = {
        "id": "p_multi_line_mismatch",
        "document_number": "INV-MULTI-002B",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2500.00"),
        "sgst_amount": Decimal("2500.00"),
        "total_invoice_value": Decimal("30000.00"),
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "tax_amount": Decimal("3000.00"),
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "tax_amount": Decimal("1500.00")  # 3000 + 1500 = 4500 != 5000 invoice tax
            }
        ]
    }

    evals_mismatch = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_mismatch,
        gstr2b_doc=g_doc_explicit,
        evaluation_date=today
    )
    food_eval_mismatch = next(e for e in evals_mismatch if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_mismatch.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert food_eval_mismatch.risk_reversal_amount == Decimal("0.00")

    # 4. Scenario C1: Missing Applicable Rates on Mixed Statutory Lines
    p_doc_no_rate = {
        "id": "p_multi_line_no_rate",
        "document_number": "INV-MULTI-003A",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2050.00"),
        "sgst_amount": Decimal("2050.00"),
        "total_invoice_value": Decimal("34100.00"),
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "taxable_value": Decimal("10000.00")
                # Missing rate: cannot assume uniform rates across catering and vehicle repair!
            }
        ]
    }

    evals_no_rate = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_no_rate,
        gstr2b_doc=g_doc_rate_aware,
        evaluation_date=today
    )
    food_eval_no_rate = next(e for e in evals_no_rate if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_no_rate.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert food_eval_no_rate.risk_reversal_amount == Decimal("0.00")

    # 5. Scenario C2: Disallowed GST-Inclusive Total Amount Basis
    p_doc_inclusive = {
        "id": "p_multi_line_inclusive",
        "document_number": "INV-MULTI-003B",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2050.00"),
        "sgst_amount": Decimal("2050.00"),
        "total_invoice_value": Decimal("34100.00"),
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "total_amount": Decimal("23600.00"),  # total_amount/line_total excluded
                "gst_rate": Decimal("18.00"),
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "total_amount": Decimal("10500.00"),
                "gst_rate": Decimal("5.00")
            }
        ]
    }

    evals_inclusive = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_inclusive,
        gstr2b_doc=g_doc_rate_aware,
        evaluation_date=today
    )
    food_eval_inclusive = next(e for e in evals_inclusive if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_inclusive.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert food_eval_inclusive.risk_reversal_amount == Decimal("0.00")

    # 6. Scenario C3: Completely Missing Line-Level Data Fallback
    p_doc_missing = {
        "id": "p_multi_line_missing",
        "document_number": "INV-MULTI-003C",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2500.00"),
        "sgst_amount": Decimal("2500.00"),
        "igst_amount": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "total_invoice_value": Decimal("30000.00"),
        "goods_receipt_date": "2026-11-02",
        "goods_receipt_note_ref": "GRN-MULTI-3",
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331"
            }
        ]
    }

    evals_missing = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_missing,
        gstr2b_doc=g_doc_explicit,
        evaluation_date=today
    )

    food_eval_missing = next(e for e in evals_missing if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_missing.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert food_eval_missing.risk_reversal_amount == Decimal("0.00")
    assert food_eval_missing.potential_itc == Decimal("5000.00")
    assert "mixed statutory treatments" in food_eval_missing.findings_explanation

    # 7. Scenario D1: Document-Level Rate (e.g. 13.67%) without Verified Uniformity Gate
    # Multi-line invoice cannot inherit document-level rate without explicit uniform rate verification
    p_doc_doc_level_unverified = {
        "id": "p_multi_line_doc_rate_unverified",
        "document_number": "INV-MULTI-004A",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2050.00"),
        "sgst_amount": Decimal("2050.00"),
        "total_invoice_value": Decimal("34100.00"),
        "gst_rate": Decimal("13.67"),  # Arbitrary / unverified blended rate on header
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "taxable_value": Decimal("10000.00")
            }
        ]
    }
    evals_doc_unverified = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_doc_level_unverified,
        gstr2b_doc=g_doc_rate_aware,
        evaluation_date=today
    )
    food_eval_doc_unverified = next(e for e in evals_doc_unverified if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_doc_unverified.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert food_eval_doc_unverified.risk_reversal_amount == Decimal("0.00")

    # 8. Scenario D2: Non-Statutory Arbitrary Rate on Lines (e.g. 13.67%)
    p_doc_non_statutory_line = {
        "id": "p_multi_line_non_statutory",
        "document_number": "INV-MULTI-004B",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2050.00"),
        "sgst_amount": Decimal("2050.00"),
        "total_invoice_value": Decimal("34100.00"),
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "gst_rate": Decimal("13.67"),  # Non-statutory rate
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "taxable_value": Decimal("10000.00"),
                "gst_rate": Decimal("13.67")
            }
        ]
    }
    evals_non_stat = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_non_statutory_line,
        gstr2b_doc=g_doc_rate_aware,
        evaluation_date=today
    )
    food_eval_non_stat = next(e for e in evals_non_stat if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_non_stat.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert food_eval_non_stat.risk_reversal_amount == Decimal("0.00")

    # 9. Scenario D3: Document-Level Rate (18.00%) WITH Authenticated Verified Uniformity Gate
    # When is_uniform_rate_verified is True AND certified by authorized verifier metadata
    # with an authentic cryptographic HMAC-SHA256 signature, valid statutory header rate is applied
    d3_sig = generate_auditor_verification_signature(
        doc_id="p_multi_line_uniform_verified",
        doc_number="INV-MULTI-004C",
        total_invoice_value=Decimal("35400.00"),
        total_tax=Decimal("5400.00"),
        header_rate=Decimal("18.00"),
        verifier_id="CA-AUDITOR-01",
        verifier_role="CHARTERED_ACCOUNTANT",
        evidence_ref="DOC-RATE-AUDIT-2026-11",
        timestamp="2026-11-23T10:00:00Z"
    )
    p_doc_uniform_verified = {
        "id": "p_multi_line_uniform_verified",
        "document_number": "INV-MULTI-004C",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2700.00"),
        "sgst_amount": Decimal("2700.00"),
        "total_invoice_value": Decimal("35400.00"),
        "gst_rate": Decimal("18.00"),
        "is_uniform_rate_verified": True,
        "rate_verification_metadata": {
            "is_verified": True,
            "verifier_role": "CHARTERED_ACCOUNTANT",
            "verifier_id": "CA-AUDITOR-01",
            "evidence_ref": "DOC-RATE-AUDIT-2026-11",
            "authorization_token": d3_sig,
            "verification_timestamp": "2026-11-23T10:00:00Z"
        },
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "taxable_value": Decimal("10000.00")
            }
        ]
    }
    g_doc_uniform = {
        "id": "g_multi_uniform",
        "section_name": "B2B",
        "itc_availability": "available",
        "central_tax": Decimal("2700.00"),
        "state_ut_tax": Decimal("2700.00"),
        "integrated_tax": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "document_number": "INV-MULTI-004C",
        "document_date": "2026-11-01"
    }
    evals_uniform = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_uniform_verified,
        gstr2b_doc=g_doc_uniform,
        evaluation_date=today
    )
    food_eval_uniform = next(e for e in evals_uniform if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_uniform.eligibility_status == ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR
    # Catering line tax = ₹10,000 * 18% = ₹1,800.00 reversed; vehicle repair ₹3,600 is exempt exception
    assert food_eval_uniform.risk_reversal_amount == Decimal("1800.00")
    assert food_eval_uniform.potential_itc == Decimal("1800.00")

    # 10. Scenario D4: Dynamic Line Rounding Tolerance Exceeded
    # 2 lines -> tolerance = 2 * 0.01 = 0.02.
    # Total invoice tax is ₹4,100.05 while theoretical tax sum is ₹4,100.00 (diff = 0.05 > 0.02)
    p_doc_tolerance_exceeded = {
        "id": "p_multi_line_tol_exceeded",
        "document_number": "INV-MULTI-004D",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2050.025"),  # Total tax = 4100.05
        "sgst_amount": Decimal("2050.025"),
        "total_invoice_value": Decimal("34100.05"),
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "gst_rate": Decimal("18.00"),  # 3600.00
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "taxable_value": Decimal("10000.00"),
                "gst_rate": Decimal("5.00")    # 500.00 -> theoretical sum = 4100.00 != 4100.05
            }
        ]
    }
    evals_tol = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_tolerance_exceeded,
        gstr2b_doc=g_doc_rate_aware,
        evaluation_date=today
    )
    food_eval_tol = next(e for e in evals_tol if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_tol.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert food_eval_tol.risk_reversal_amount == Decimal("0.00")

    # 11. Scenario D5: Legitimate 40.00% Statutory Rate (Notification No. 9/2025-Integrated Tax (Rate))
    # Authentic tariff item 2106 90 20 (Pan Masala) taxed at 40% alongside 18% repair
    p_doc_40_pct = {
        "id": "p_multi_line_40_pct",
        "document_number": "INV-MULTI-004E",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("3800.00"),  # Total tax = 7600.00 (3600 repair + 4000 pan masala)
        "sgst_amount": Decimal("3800.00"),
        "total_invoice_value": Decimal("37600.00"),
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "gst_rate": Decimal("18.00"),  # 3600.00 tax
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Pan masala executive refreshments",
                "hsn_sac_code": "21069020",    # Authentic tariff item 2106 90 20
                "taxable_value": Decimal("10000.00"),
                "gst_rate": Decimal("40.00")   # Legitimate 40.00% rate under Notification 9/2025 -> 4000.00 tax
            }
        ]
    }
    g_doc_40_pct = {
        "id": "g_multi_40",
        "section_name": "B2B",
        "itc_availability": "available",
        "central_tax": Decimal("3800.00"),
        "state_ut_tax": Decimal("3800.00"),
        "integrated_tax": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "document_number": "INV-MULTI-004E",
        "document_date": "2026-11-01"
    }
    evals_40 = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_40_pct,
        gstr2b_doc=g_doc_40_pct,
        evaluation_date=today
    )
    food_eval_40 = next(e for e in evals_40 if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_40.eligibility_status == ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR
    assert food_eval_40.risk_reversal_amount == Decimal("4000.00")
    assert food_eval_40.potential_itc == Decimal("4000.00")

    # 12. Scenario D6: Legitimate 0.25% Statutory Rate (Schedule V - Diamonds/Precious Stones)
    # Rate input '0.25%' on authentic HSN 7102 (Diamonds) must NOT be multiplied by 100 to 25.00%!
    p_doc_025_pct = {
        "id": "p_multi_line_025_pct",
        "document_number": "INV-MULTI-004F",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("1812.50"),  # Total tax = 3625.00 (3600 repair + 25 diamonds)
        "sgst_amount": Decimal("1812.50"),
        "total_invoice_value": Decimal("33625.00"),
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "gst_rate": Decimal("18.00"),  # 3600.00
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Cut and polished diamonds for corporate gift",
                "hsn_sac_code": "7102",        # Authentic Schedule V HSN
                "taxable_value": Decimal("10000.00"),
                "gst_rate": "0.25%"            # Correctly recognized as 0.25% (₹25 tax), NOT 25.00%!
            }
        ]
    }
    g_doc_025_pct = {
        "id": "g_multi_025",
        "section_name": "B2B",
        "itc_availability": "available",
        "central_tax": Decimal("1812.50"),
        "state_ut_tax": Decimal("1812.50"),
        "integrated_tax": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "document_number": "INV-MULTI-004F",
        "document_date": "2026-11-01"
    }
    evals_025 = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_025_pct,
        gstr2b_doc=g_doc_025_pct,
        evaluation_date=today
    )
    gift_eval_025 = next(e for e in evals_025 if e.rule_code == "SEC_17_5_PERSONAL")
    assert gift_eval_025.eligibility_status == ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR
    assert gift_eval_025.risk_reversal_amount == Decimal("25.00")
    assert gift_eval_025.potential_itc == Decimal("25.00")

    # 13. Scenario D7: Rejection of String Values, Raw Unauthenticated Booleans, and Mismatched HSN Rates
    # A. String values ("false", "False", "0", "true", "yes") MUST NOT activate uniform rate fallback
    for false_str_val in ["false", "False", "0", "true", "yes"]:
        p_doc_string_trap = {
            "id": f"p_multi_trap_{false_str_val}",
            "document_number": f"INV-TRAP-{false_str_val}",
            "document_date": "2026-11-01",
            "cgst_amount": Decimal("2700.00"),
            "sgst_amount": Decimal("2700.00"),
            "total_invoice_value": Decimal("35400.00"),
            "gst_rate": Decimal("18.00"),
            "is_uniform_rate_verified": false_str_val,  # String trap!
            "line_items": [
                {
                    "item_description": "Factory vehicle test track repair",
                    "hsn_sac_code": "998729",
                    "taxable_value": Decimal("20000.00"),
                    "facts": {
                        "is_vehicle_manufacturer": True,
                        "manufacturing_license": True,
                        "relates_to_manufactured_vehicles": True,
                        "is_factual_context_verified": True
                    }
                },
                {
                    "item_description": "Executive corporate catering dinner",
                    "hsn_sac_code": "996331",
                    "taxable_value": Decimal("10000.00")
                }
            ]
        }
        evals_trap = engine.evaluate_itc_eligibility(
            purchase_doc=p_doc_string_trap,
            gstr2b_doc=g_doc_uniform,
            evaluation_date=today
        )
        food_eval_trap = next(e for e in evals_trap if e.rule_code == "SEC_17_5_FOOD")
        assert food_eval_trap.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED, (
            f"String value '{false_str_val}' accidentally activated uniform rate fallback!"
        )
        assert food_eval_trap.risk_reversal_amount == Decimal("0.00")

    # B. Raw unauthenticated boolean True alone without auditor metadata MUST be rejected
    p_doc_raw_bool_unauthenticated = {
        "id": "p_multi_raw_bool",
        "document_number": "INV-RAW-BOOL",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2700.00"),
        "sgst_amount": Decimal("2700.00"),
        "total_invoice_value": Decimal("35400.00"),
        "gst_rate": Decimal("18.00"),
        "is_uniform_rate_verified": True,  # Raw boolean alone without auditor metadata!
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "taxable_value": Decimal("10000.00")
            }
        ]
    }
    evals_raw_bool = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_raw_bool_unauthenticated,
        gstr2b_doc=g_doc_uniform,
        evaluation_date=today
    )
    food_eval_raw_bool = next(e for e in evals_raw_bool if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_raw_bool.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED, (
        "Raw client boolean True alone should be rejected without verified auditor metadata!"
    )
    assert food_eval_raw_bool.risk_reversal_amount == Decimal("0.00")

    # C. Fabricated verifier credentials (unauthorized role or missing auth token) MUST be rejected
    p_doc_fake_credentials = {
        "id": "p_multi_fake_cred",
        "document_number": "INV-FAKE-CRED",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2700.00"),
        "sgst_amount": Decimal("2700.00"),
        "total_invoice_value": Decimal("35400.00"),
        "gst_rate": Decimal("18.00"),
        "rate_verification_metadata": {
            "is_verified": True,
            "verifier_role": "ACCOUNTANT",  # NOT in AUTHORIZED_VERIFIER_ROLES!
            "verifier_id": "FAKE_ID",
            "evidence_ref": "REF-01"
            # Missing authorization_token!
        },
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "taxable_value": Decimal("10000.00")
            }
        ]
    }
    evals_fake_cred = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_fake_credentials,
        gstr2b_doc=g_doc_uniform,
        evaluation_date=today
    )
    food_eval_fake_cred = next(e for e in evals_fake_cred if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_fake_cred.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert food_eval_fake_cred.risk_reversal_amount == Decimal("0.00")

    # D. Mismatched HSN Rates MUST be rejected under statutory schedule
    # E.g. 40% rate erroneously assigned to catering (HSN 996331) or biris (HSN 24031921)
    p_doc_mismatched_hsn_rate = {
        "id": "p_multi_hsn_mismatch",
        "document_number": "INV-HSN-MISMATCH",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("3800.00"),
        "sgst_amount": Decimal("3800.00"),
        "total_invoice_value": Decimal("37600.00"),
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "gst_rate": Decimal("18.00"),
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "taxable_value": Decimal("10000.00"),
                "gst_rate": Decimal("40.00")   # INVALID: Catering is 5%/18%, NEVER 40%!
            }
        ]
    }
    evals_hsn_mismatch = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_mismatched_hsn_rate,
        gstr2b_doc=g_doc_40_pct,
        evaluation_date=today
    )
    food_eval_hsn_mismatch = next(e for e in evals_hsn_mismatch if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_hsn_mismatch.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert food_eval_hsn_mismatch.risk_reversal_amount == Decimal("0.00")

    # 14. Scenario D8: Structured Verification Record Acceptance
    d8_sig = generate_auditor_verification_signature(
        doc_id="p_multi_structured_verified",
        doc_number="INV-STRUCTURED-VERIFIED",
        total_invoice_value=Decimal("35400.00"),
        total_tax=Decimal("5400.00"),
        header_rate=Decimal("18.00"),
        verifier_id="CA-AUDITOR-01",
        verifier_role="CHARTERED_ACCOUNTANT",
        evidence_ref="DOC-RATE-AUDIT-2026-11",
        timestamp="2026-11-23T10:00:00Z"
    )
    p_doc_structured_verified = {
        "id": "p_multi_structured_verified",
        "document_number": "INV-STRUCTURED-VERIFIED",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2700.00"),
        "sgst_amount": Decimal("2700.00"),
        "total_invoice_value": Decimal("35400.00"),
        "gst_rate": Decimal("18.00"),
        "is_uniform_rate_verified": {
            "is_verified": True,
            "verifier_role": "CHARTERED_ACCOUNTANT",
            "verifier_id": "CA-AUDITOR-01",
            "evidence_ref": "DOC-RATE-AUDIT-2026-11",
            "authorization_token": d8_sig,
            "verification_timestamp": "2026-11-23T10:00:00Z"
        },
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "taxable_value": Decimal("10000.00")
            }
        ]
    }
    evals_struct = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_structured_verified,
        gstr2b_doc=g_doc_uniform,
        evaluation_date=today
    )
    food_eval_struct = next(e for e in evals_struct if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_struct.eligibility_status == ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR
    assert food_eval_struct.risk_reversal_amount == Decimal("1800.00")

    # 15. Scenario D9: Explicit Rate Representation Contract
    # A. Dedicated explicit rate_fraction field (e.g. rate_fraction: 0.18 -> 18.00%)
    p_doc_explicit_fraction = {
        "id": "p_multi_rate_fraction",
        "document_number": "INV-RATE-FRACTION",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2050.00"),
        "sgst_amount": Decimal("2050.00"),
        "total_invoice_value": Decimal("34100.00"),
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "rate_fraction": Decimal("0.18"),  # Explicit fractional representation -> 18.00%
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "taxable_value": Decimal("10000.00"),
                "rate_fraction": Decimal("0.05")   # Explicit fractional representation -> 5.00%
            }
        ]
    }
    evals_frac = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_explicit_fraction,
        gstr2b_doc=g_doc_rate_aware,
        evaluation_date=today
    )
    food_eval_frac = next(e for e in evals_frac if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_frac.eligibility_status == ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR
    assert food_eval_frac.risk_reversal_amount == Decimal("500.00")

    # B. Ambiguous unscaled decimal in standard percentage field (gst_rate: 0.18) MUST be rejected!
    # Because 0.18% is not a statutory rate and guessing heuristics are eliminated
    p_doc_ambiguous_decimal = {
        "id": "p_multi_ambiguous_decimal",
        "document_number": "INV-AMBIGUOUS-DECIMAL",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2050.00"),
        "sgst_amount": Decimal("2050.00"),
        "total_invoice_value": Decimal("34100.00"),
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "gst_rate": Decimal("0.18"),       # Ambiguous unscaled decimal in percentage field!
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True
                }
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "996331",
                "taxable_value": Decimal("10000.00"),
                "gst_rate": Decimal("5.00")
            }
        ]
    }
    evals_ambiguous = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_ambiguous_decimal,
        gstr2b_doc=g_doc_rate_aware,
        evaluation_date=today
    )
    food_eval_ambiguous = next(e for e in evals_ambiguous if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_ambiguous.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED, (
        "Ambiguous decimal 0.18 in standard percentage field should be rejected without guessing!"
    )
    assert food_eval_ambiguous.risk_reversal_amount == Decimal("0.00")


# ============================================================================
# 7. STATUTORY TARIFF SCHEDULES, NOTIFICATIONS 19/2025 & 01/2026, DATE BOUNDARIES
# ============================================================================

def test_statutory_tariff_effective_date_and_subheadings():
    """
    Validates exact statutory dates and tariff classifications under:
    - Notification No. 19/2025-Integrated Tax (Rate) & Central Tax (Rate)
    - Notification No. 01/2026-Integrated Tax (Rate) & Central Tax (Rate)
    - Corrigendum G.S.R. 339(E) dated 6 May 2026
    """
    from datetime import date
    from app.modules.reconciliation.itc_engine import validate_hsn_statutory_rate

    pre_feb_2026 = date(2026, 1, 15)
    post_feb_2026 = date(2026, 2, 15)
    pre_may_2026 = date(2026, 4, 15)
    post_may_2026 = date(2026, 5, 15)

    # 1. Pan Masala (2106 90 20)
    # Pre-01.02.2026: 28% valid, 40% rejected
    valid, _ = validate_hsn_statutory_rate(Decimal("28.00"), "21069020", pre_feb_2026)
    assert valid is True
    valid, msg = validate_hsn_statutory_rate(Decimal("40.00"), "21069020", pre_feb_2026)
    assert valid is False
    assert "prior to 01.02.2026" in msg and "40% not yet in force" in msg

    # Post-01.02.2026: 40% valid, 28% rejected (Schedule VII omission)
    valid, _ = validate_hsn_statutory_rate(Decimal("40.00"), "21069020", post_feb_2026)
    assert valid is True
    valid, msg = validate_hsn_statutory_rate(Decimal("28.00"), "21069020", post_feb_2026)
    assert valid is False
    assert "28% omitted" in msg

    # General food preparations under heading 2106: 40% strictly rejected
    valid, msg = validate_hsn_statutory_rate(Decimal("40.00"), "2106", post_feb_2026)
    assert valid is False
    assert "restricted to tariff item 2106 90 20" in msg

    # 2. Biris (2403 19 21 / 2403 19 29)
    # Pre-01.02.2026: 28% valid, 18% and 40% rejected
    valid, _ = validate_hsn_statutory_rate(Decimal("28.00"), "24031921", pre_feb_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("18.00"), "24031921", pre_feb_2026)
    assert valid is False
    valid, _ = validate_hsn_statutory_rate(Decimal("40.00"), "24031921", pre_feb_2026)
    assert valid is False

    # Post-01.02.2026: moved to 18% schedule. 28% and 40% strictly rejected.
    valid, _ = validate_hsn_statutory_rate(Decimal("18.00"), "24031921", post_feb_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("18.00"), "24031929", post_feb_2026)
    assert valid is True
    valid, msg = validate_hsn_statutory_rate(Decimal("40.00"), "24031921", post_feb_2026)
    assert valid is False
    assert "statutory rate is 18%" in msg
    valid, _ = validate_hsn_statutory_rate(Decimal("28.00"), "24031921", post_feb_2026)
    assert valid is False

    # 3. Manufactured Tobacco (2403, non-biri) & Cigarettes (2402)
    # Exact 8-digit tariff items (2403 11 10, 2403 19 10, 2403 99 90): Post-01.02.2026: 40% valid, 28% rejected
    valid, _ = validate_hsn_statutory_rate(Decimal("40.00"), "24039990", post_feb_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("40.00"), "24031910", post_feb_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("28.00"), "24039990", post_feb_2026)
    assert valid is False

    # Incomplete 2403 / 240319 / 240399 fails safe to AMBIGUOUS_TARIFF
    for amb_2403 in ["2403", "240319", "240399"]:
        valid, msg = validate_hsn_statutory_rate(Decimal("40.00"), amb_2403, post_feb_2026)
        assert valid is False
        assert "AMBIGUOUS_TARIFF" in msg

    # Cigarettes (2402)
    valid, _ = validate_hsn_statutory_rate(Decimal("40.00"), "240220", post_feb_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("28.00"), "240220", post_feb_2026)
    assert valid is False

    # 4. Heading 2401 (Unmanufactured tobacco leaves vs refuse)
    # Leaves (2401 10, 2401 20): strictly 5%
    valid, _ = validate_hsn_statutory_rate(Decimal("5.00"), "240110", post_feb_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("5.00"), "240120", post_feb_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("40.00"), "240110", post_feb_2026)
    assert valid is False
    # Refuse (2401 30 00): 40% post-01.02.2026, 28% pre-Feb 2026
    valid, _ = validate_hsn_statutory_rate(Decimal("40.00"), "24013000", post_feb_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("28.00"), "24013000", pre_feb_2026)
    assert valid is True
    # Incomplete 2401 fails safe to AMBIGUOUS_TARIFF
    valid, msg = validate_hsn_statutory_rate(Decimal("5.00"), "2401", post_feb_2026)
    assert valid is False
    assert "AMBIGUOUS_TARIFF" in msg

    # 5. Heading 2404 (Novel tobacco and nicotine products)
    # Inhalation products (2404 11 00, 2404 19 00): 40% post-01.02.2026, 28% pre-Feb 2026
    valid, _ = validate_hsn_statutory_rate(Decimal("40.00"), "24041100", post_feb_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("40.00"), "24041900", post_feb_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("28.00"), "24041100", pre_feb_2026)
    assert valid is True
    # Other nicotine (2404 12 00 e-liquids, 2404 91 00 oral, 2404 92 00 transdermal): strictly 18%
    valid, _ = validate_hsn_statutory_rate(Decimal("18.00"), "24041200", post_feb_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("18.00"), "24049100", post_feb_2026)
    assert valid is True
    valid, msg = validate_hsn_statutory_rate(Decimal("40.00"), "24041200", post_feb_2026)
    assert valid is False
    assert "statutory rate is 18%" in msg
    # Broad 2404 fails safe to AMBIGUOUS_TARIFF
    valid, msg = validate_hsn_statutory_rate(Decimal("40.00"), "2404", post_feb_2026)
    assert valid is False
    assert "AMBIGUOUS_TARIFF" in msg

    # 6. Beverages (2202 Subheadings under Notification No. 01/2026 & Corrigendum G.S.R. 339(E))
    # Non-alcoholic beer (2202 91 00 per Corrigendum) & Caffeinated drinks (2202 99 91, 2202 99 99):
    # Pre-01.05.2026: 28% valid, 40% rejected
    valid, _ = validate_hsn_statutory_rate(Decimal("28.00"), "22029100", pre_may_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("28.00"), "22029991", pre_may_2026)
    assert valid is True
    valid, msg = validate_hsn_statutory_rate(Decimal("40.00"), "22029100", pre_may_2026)
    assert valid is False
    assert "prior to 01.05.2026" in msg and "40% not in force" in msg

    # Post-01.05.2026: 40% valid, 28% rejected
    valid, _ = validate_hsn_statutory_rate(Decimal("40.00"), "22029100", post_may_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("40.00"), "22029991", post_may_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("40.00"), "22029999", post_may_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("28.00"), "22029100", post_may_2026)
    assert valid is False

    # Aerated waters containing added sugar (2202 10 10, 2202 10 20, 2202 10 90, 2202 10):
    # Statutorily maintained at 28% (40% strictly prohibited)
    valid, _ = validate_hsn_statutory_rate(Decimal("28.00"), "22021010", post_may_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("28.00"), "220210", post_may_2026)
    assert valid is True
    valid, msg = validate_hsn_statutory_rate(Decimal("40.00"), "22021010", post_may_2026)
    assert valid is False
    assert "40% is prohibited" in msg

    # Fruit juice / milk-based beverages (2202 90, 2202 99 10, 2202 99 21):
    # Strictly 5% or 12%; 28% and 40% strictly rejected
    valid, _ = validate_hsn_statutory_rate(Decimal("5.00"), "22029910", post_may_2026)
    assert valid is True
    valid, _ = validate_hsn_statutory_rate(Decimal("12.00"), "22029921", post_may_2026)
    assert valid is True
    valid, msg = validate_hsn_statutory_rate(Decimal("40.00"), "22029910", post_may_2026)
    assert valid is False
    assert "statutory rate is 5% or 12%" in msg
    valid, _ = validate_hsn_statutory_rate(Decimal("28.00"), "22029921", post_may_2026)
    assert valid is False

    # Broad heading 2202 without subheading: fails safe to review
    valid, msg = validate_hsn_statutory_rate(Decimal("40.00"), "2202", post_may_2026)
    assert valid is False
    assert "AMBIGUOUS_TARIFF" in msg


# ============================================================================
# 8. SERVER-SIDE CRYPTOGRAPHIC HMAC-SHA256 & AUDITOR REGISTRY AUTHENTICATION
# ============================================================================

def test_statutory_cryptographic_hmac_and_auditor_registry():
    """
    Validates server-side cryptographic HMAC-SHA256 signature verification
    and trusted statutory auditor registry lookup for uniform-rate multi-line gating:
    - Fail-closed security (missing key, short key <32 chars).
    - Identity & credential verification (ICAI format, FRN format, COP status, expiry).
    - Revoked auditor rejection.
    - Timestamp freshness window (max 24h) and future clock skew (max 5m).
    - Cryptographic HMAC-SHA256 tamper detection.
    - Replay protection (preventing cross-document signature reuse).
    """
    import os
    from datetime import datetime, timezone, timedelta
    from app.modules.reconciliation.itc_engine import (
        _is_strictly_verified_uniform_rate,
        generate_auditor_verification_signature,
        TRUSTED_STATUTORY_AUDITOR_REGISTRY,
        reset_audit_signature_cache_for_testing,
    )

    reset_audit_signature_cache_for_testing()

    test_key = "MYCA_TEST_SUITE_STATUTORY_AUDIT_SECRET_2026_MIN32CHAR"
    os.environ["STATUTORY_AUDIT_SIGNING_KEY"] = test_key

    doc_id = "INV-DOC-AUTH-001"
    doc_number = "INV/2026/001"
    inv_val = Decimal("118000.00")
    cgst = Decimal("9000.00")
    sgst = Decimal("9000.00")
    total_tax = cgst + sgst
    header_rate = Decimal("18.00")
    verifier_id = "CA-AUDITOR-01"
    verifier_role = "CHARTERED_ACCOUNTANT"
    evidence_ref = "AUDIT-CERT-2026-99"
    ts = datetime.now(timezone.utc).isoformat()

    valid_sig = generate_auditor_verification_signature(
        doc_id=doc_id,
        doc_number=doc_number,
        total_invoice_value=inv_val,
        total_tax=total_tax,
        header_rate=header_rate,
        verifier_id=verifier_id,
        verifier_role=verifier_role,
        evidence_ref=evidence_ref,
        timestamp=ts,
    )

    base_doc = {
        "id": doc_id,
        "document_number": doc_number,
        "total_invoice_value": inv_val,
        "cgst_amount": cgst,
        "sgst_amount": sgst,
        "gst_rate": header_rate,
        "is_uniform_rate_verified": True,
        "rate_verification_metadata": {
            "is_verified": True,
            "verifier_role": verifier_role,
            "verifier_id": verifier_id,
            "evidence_ref": evidence_ref,
            "verification_timestamp": ts,
            "authorization_token": valid_sig,
        },
    }

    # 1. Valid authentic signature and registered active CA -> Accepted
    assert _is_strictly_verified_uniform_rate(base_doc) is True

    # 2. Fabricated signature token -> Rejected
    fake_sig_doc = dict(base_doc)
    fake_sig_doc["rate_verification_metadata"] = dict(base_doc["rate_verification_metadata"])
    fake_sig_doc["rate_verification_metadata"]["authorization_token"] = "TOKEN-AUTH-SIG-998877665544332211"
    assert _is_strictly_verified_uniform_rate(fake_sig_doc) is False

    # 3. Tampered invoice value (e.g. 118000 -> 120000) -> Signature mismatch -> Rejected
    tampered_doc = dict(base_doc)
    tampered_doc["total_invoice_value"] = Decimal("120000.00")
    assert _is_strictly_verified_uniform_rate(tampered_doc) is False

    # 4. Unregistered auditor ID (not in TRUSTED_STATUTORY_AUDITOR_REGISTRY) -> Rejected
    unreg_doc = dict(base_doc)
    unreg_doc["rate_verification_metadata"] = dict(base_doc["rate_verification_metadata"])
    unreg_doc["rate_verification_metadata"]["verifier_id"] = "CA-FAKE-99"
    unreg_sig = generate_auditor_verification_signature(
        doc_id=doc_id, doc_number=doc_number, total_invoice_value=inv_val,
        total_tax=total_tax, header_rate=header_rate, verifier_id="CA-FAKE-99",
        verifier_role=verifier_role, evidence_ref=evidence_ref, timestamp=ts
    )
    unreg_doc["rate_verification_metadata"]["authorization_token"] = unreg_sig
    assert _is_strictly_verified_uniform_rate(unreg_doc) is False

    # 5. Revoked auditor ID (in REVOKED_STATUTORY_AUDITOR_IDS) -> Rejected
    revoked_doc = dict(base_doc)
    revoked_doc["rate_verification_metadata"] = dict(base_doc["rate_verification_metadata"])
    revoked_doc["rate_verification_metadata"]["verifier_id"] = "CA-REVOKED-99"
    assert _is_strictly_verified_uniform_rate(revoked_doc) is False

    # 6. Role mismatch (auditor is CHARTERED_ACCOUNTANT but claims STATUTORY_AUDITOR) -> Rejected
    role_mismatch_doc = dict(base_doc)
    role_mismatch_doc["rate_verification_metadata"] = dict(base_doc["rate_verification_metadata"])
    role_mismatch_doc["rate_verification_metadata"]["verifier_role"] = "STATUTORY_AUDITOR"
    assert _is_strictly_verified_uniform_rate(role_mismatch_doc) is False

    # 7. Raw client boolean True alone without metadata -> Rejected
    raw_bool_doc = {
        "id": doc_id,
        "is_uniform_rate_verified": True,
    }
    assert _is_strictly_verified_uniform_rate(raw_bool_doc) is False

    # 8. String values ("false", "true", "0", "1") -> Rejected
    for str_val in ["false", "true", "0", "1", "yes"]:
        str_trap_doc = {
            "id": doc_id,
            "is_uniform_rate_verified": str_val,
            "rate_verification_metadata": base_doc["rate_verification_metadata"],
        }
        assert _is_strictly_verified_uniform_rate(str_trap_doc) is False

    # 9. Fail-Closed Security: Missing signing secret in environment -> Rejected
    del os.environ["STATUTORY_AUDIT_SIGNING_KEY"]
    assert _is_strictly_verified_uniform_rate(base_doc) is False

    # 10. Fail-Closed Security: Secret shorter than 32 characters -> Rejected
    os.environ["STATUTORY_AUDIT_SIGNING_KEY"] = "short_insecure_secret_12345"
    assert _is_strictly_verified_uniform_rate(base_doc) is False

    # Restore valid test key
    os.environ["STATUTORY_AUDIT_SIGNING_KEY"] = test_key

    # 11. Expired Timestamp (> 24 hours old) -> Rejected
    expired_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    exp_sig = generate_auditor_verification_signature(
        doc_id=doc_id, doc_number=doc_number, total_invoice_value=inv_val,
        total_tax=total_tax, header_rate=header_rate, verifier_id=verifier_id,
        verifier_role=verifier_role, evidence_ref=evidence_ref, timestamp=expired_ts
    )
    expired_doc = dict(base_doc)
    expired_doc["rate_verification_metadata"] = dict(base_doc["rate_verification_metadata"])
    expired_doc["rate_verification_metadata"]["verification_timestamp"] = expired_ts
    expired_doc["rate_verification_metadata"]["authorization_token"] = exp_sig
    assert _is_strictly_verified_uniform_rate(expired_doc) is False

    # 12. Future Timestamp (> 5 minutes clock skew) -> Rejected
    future_ts = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    fut_sig = generate_auditor_verification_signature(
        doc_id=doc_id, doc_number=doc_number, total_invoice_value=inv_val,
        total_tax=total_tax, header_rate=header_rate, verifier_id=verifier_id,
        verifier_role=verifier_role, evidence_ref=evidence_ref, timestamp=future_ts
    )
    future_doc = dict(base_doc)
    future_doc["rate_verification_metadata"] = dict(base_doc["rate_verification_metadata"])
    future_doc["rate_verification_metadata"]["verification_timestamp"] = future_ts
    future_doc["rate_verification_metadata"]["authorization_token"] = fut_sig
    assert _is_strictly_verified_uniform_rate(future_doc) is False

    # 13. Replay Protection: Token issued for doc1 replayed on doc2 -> Rejected
    doc2_doc = dict(base_doc)
    doc2_doc["id"] = "INV-DOC-AUTH-002"
    doc2_doc["document_number"] = "INV/2026/002"
    # Even if total tax and value match, token was already bound to doc1
    assert _is_strictly_verified_uniform_rate(doc2_doc) is False


# ============================================================================
# 9. STRICT DECIMAL PRECISION AND EXPLICIT COMPONENT RATE CONTRACT
# ============================================================================

def test_statutory_decimal_precision_and_component_rates():
    """
    Validates:
    - Excess decimal precision beyond 2 decimal places is rejected (no silent rounding).
    - Fractional inputs with excess precision are rejected.
    - Explicit component rates (cgst_rate + sgst_rate or igst_rate) aggregate to total GST rate.
    - Standalone ambiguous component rates (14% or 20% in gst_rate) are rejected.
    """
    from datetime import date
    from app.modules.reconciliation.itc_engine import (
        _extract_line_rate,
        validate_hsn_statutory_rate,
    )

    eval_date = date(2026, 11, 23)

    # 1. Excess precision in percentage point inputs: rejected
    for excess_rate in [Decimal("18.004"), Decimal("0.254"), Decimal("5.001"), Decimal("12.0001")]:
        valid, msg = validate_hsn_statutory_rate(excess_rate, "998729", eval_date)
        assert valid is False
        assert "EXCESS_PRECISION" in msg

        line_excess = {"gst_rate": excess_rate, "hsn_sac_code": "998729"}
        assert _extract_line_rate(line_excess, document_date=eval_date) is None

    # 2. Excess precision in fractional inputs: rejected
    line_frac_excess = {"rate_fraction": Decimal("0.18004"), "hsn_sac_code": "998729"}
    assert _extract_line_rate(line_frac_excess, document_date=eval_date) is None

    # 3. Valid precision percentage point and fractional inputs: accepted
    line_valid_pct = {"gst_rate": Decimal("18.00"), "hsn_sac_code": "998729"}
    assert _extract_line_rate(line_valid_pct, document_date=eval_date) == Decimal("18.00")

    line_valid_frac = {"rate_fraction": Decimal("0.18"), "hsn_sac_code": "998729"}
    assert _extract_line_rate(line_valid_frac, document_date=eval_date) == Decimal("18.00")

    # 4. Component Rate Aggregation:
    # cgst_rate (9.00) + sgst_rate (9.00) aggregates to 18.00%
    line_component = {
        "cgst_rate": Decimal("9.00"),
        "sgst_rate": Decimal("9.00"),
        "hsn_sac_code": "998729",
    }
    assert _extract_line_rate(line_component, document_date=eval_date) == Decimal("18.00")

    # igst_rate (5.00) used directly
    line_igst = {
        "igst_rate": Decimal("5.00"),
        "hsn_sac_code": "996331",
    }
    assert _extract_line_rate(line_igst, document_date=eval_date) == Decimal("5.00")

    # 5. Standalone ambiguous component rates (14% or 20% in gst_rate) rejected
    for amb_rate in [Decimal("14.00"), Decimal("20.00")]:
        line_amb = {"gst_rate": amb_rate, "hsn_sac_code": "998729"}
        assert _extract_line_rate(line_amb, document_date=eval_date) is None


# ============================================================================
# 10. ZERO-TRUST FAIL-SAFE CLASSIFICATION (MISSING OR UNKNOWN HSN)
# ============================================================================

def test_statutory_zero_trust_missing_or_unknown_hsn():
    """
    Validates zero-trust classification:
    - Missing HSN ("" or None) fails safe to CA review with 0.00 risk reversal.
    - Unknown / uncatalogued HSN ("999999") fails safe to CA review with 0.00 risk reversal.
    - Multi-line invoice with missing HSN line cannot allocate tax deterministically,
      routing to REVIEW_REQUIRED with 0.00 reversal.
    """
    from datetime import date
    from app.modules.reconciliation.itc_engine import (
        StatutoryITCRiskEngine,
        validate_hsn_statutory_rate,
        _extract_line_rate,
    )
    from app.modules.reconciliation.schemas import ITCEligibilityEnum

    engine = StatutoryITCRiskEngine()
    today = date(2026, 11, 23)

    # 1. validate_hsn_statutory_rate fails safe on missing or unknown HSN
    valid, msg = validate_hsn_statutory_rate(Decimal("18.00"), hsn_sac=None)
    assert valid is False
    assert "MISSING_HSN" in msg

    valid, msg = validate_hsn_statutory_rate(Decimal("18.00"), hsn_sac="")
    assert valid is False
    assert "MISSING_HSN" in msg

    valid, msg = validate_hsn_statutory_rate(Decimal("18.00"), hsn_sac="999999")
    assert valid is False
    assert "UNKNOWN_HSN" in msg

    # 2. _extract_line_rate fails safe on missing or unknown HSN
    line_missing_hsn = {"gst_rate": Decimal("18.00"), "hsn_sac_code": ""}
    assert _extract_line_rate(line_missing_hsn, document_date=today) is None

    line_unknown_hsn = {"gst_rate": Decimal("18.00"), "hsn_sac_code": "999999"}
    assert _extract_line_rate(line_unknown_hsn, document_date=today) is None

    # 3. End-to-end multi-line invoice evaluation with missing HSN line
    p_doc_missing_hsn = {
        "id": "p_multi_missing_hsn",
        "document_number": "INV-MISSING-HSN",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("2050.00"),
        "sgst_amount": Decimal("2050.00"),
        "total_invoice_value": Decimal("34100.00"),
        "line_items": [
            {
                "item_description": "Factory vehicle test track repair",
                "hsn_sac_code": "998729",
                "taxable_value": Decimal("20000.00"),
                "gst_rate": Decimal("18.00"),
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True,
                },
            },
            {
                "item_description": "Executive corporate catering dinner",
                "hsn_sac_code": "",  # MISSING HSN!
                "taxable_value": Decimal("10000.00"),
                "gst_rate": Decimal("5.00"),
            },
        ],
    }
    g_doc = {
        "id": "g_multi_missing_hsn",
        "section_name": "B2B",
        "itc_availability": "available",
        "central_tax": Decimal("2050.00"),
        "state_ut_tax": Decimal("2050.00"),
        "integrated_tax": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "document_number": "INV-MISSING-HSN",
        "document_date": "2026-11-01",
    }
    evals = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_missing_hsn,
        gstr2b_doc=g_doc,
        evaluation_date=today,
    )
    food_eval = next(e for e in evals if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert food_eval.risk_reversal_amount == Decimal("0.00")

    # 4. Multi-line invoice with explicit line taxes where one line has missing HSN
    p_doc_explicit_missing_hsn = {
        "id": "p_multi_expl_missing_hsn",
        "document_number": "INV-EXPL-MISSING-HSN",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("1800.00"),
        "sgst_amount": Decimal("1800.00"),
        "total_invoice_value": Decimal("23600.00"),
        "line_items": [
            {
                "item_description": "Factory vehicle maintenance",
                "hsn_sac_code": "998729",
                "tax_amount": Decimal("1800.00"),
                "facts": {
                    "is_vehicle_manufacturer": True,
                    "manufacturing_license": True,
                    "relates_to_manufactured_vehicles": True,
                    "is_factual_context_verified": True,
                },
            },
            {
                "item_description": "Office party catering",
                "hsn_sac_code": "",  # MISSING HSN!
                "tax_amount": Decimal("1800.00"),
            },
        ],
    }
    g_doc_expl = {
        "id": "g_multi_expl",
        "section_name": "B2B",
        "itc_availability": "available",
        "central_tax": Decimal("1800.00"),
        "state_ut_tax": Decimal("1800.00"),
        "integrated_tax": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "document_number": "INV-EXPL-MISSING-HSN",
        "document_date": "2026-11-01",
    }
    evals_expl = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_explicit_missing_hsn,
        gstr2b_doc=g_doc_expl,
        evaluation_date=today,
    )
    food_eval_expl = next(e for e in evals_expl if e.rule_code == "SEC_17_5_FOOD")
    assert food_eval_expl.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert food_eval_expl.risk_reversal_amount == Decimal("0.00")

    # 5. Single-line invoice with missing HSN -> routes to STATUTORY_CLASSIFICATION_REVIEW
    p_doc_single_missing = {
        "id": "p_single_missing",
        "document_number": "INV-SINGLE-MISSING",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("900.00"),
        "sgst_amount": Decimal("900.00"),
        "total_invoice_value": Decimal("11800.00"),
        "hsn_sac_code": "",
        "item_description": "General Consulting Services",
        "goods_receipt_date": "2026-11-02",
        "goods_receipt_note_ref": "GRN-SINGLE-01",
    }
    g_doc_single = {
        "id": "g_single_valid",
        "section_name": "B2B",
        "itc_availability": "available",
        "central_tax": Decimal("900.00"),
        "state_ut_tax": Decimal("900.00"),
        "integrated_tax": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "document_number": "INV-SINGLE-MISSING",
        "document_date": "2026-11-01",
    }
    evals_single_missing = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_single_missing,
        gstr2b_doc=g_doc_single,
        evaluation_date=today,
    )
    class_eval_missing = next(e for e in evals_single_missing if e.rule_code == "STATUTORY_CLASSIFICATION_REVIEW")
    assert class_eval_missing.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert class_eval_missing.risk_reversal_amount == Decimal("0.00")

    # 6. Single-line invoice with unknown / uncatalogued HSN -> routes to STATUTORY_CLASSIFICATION_REVIEW
    p_doc_single_unknown = dict(p_doc_single_missing)
    p_doc_single_unknown["id"] = "p_single_unknown"
    p_doc_single_unknown["hsn_sac_code"] = "999999"
    evals_single_unknown = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_single_unknown,
        gstr2b_doc=g_doc_single,
        evaluation_date=today,
    )
    class_eval_unknown = next(e for e in evals_single_unknown if e.rule_code == "STATUTORY_CLASSIFICATION_REVIEW")
    assert class_eval_unknown.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert class_eval_unknown.risk_reversal_amount == Decimal("0.00")

    # 7. Single-line invoice with valid catalogued HSN and valid rate -> routes to SEC_16_COMPLETE
    p_doc_single_valid = dict(p_doc_single_missing)
    p_doc_single_valid["id"] = "p_single_valid"
    p_doc_single_valid["hsn_sac_code"] = "998311"
    p_doc_single_valid["gst_rate"] = Decimal("18.00")
    p_doc_single_valid["item_description"] = "General Technical Consulting"
    evals_single_valid = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_single_valid,
        gstr2b_doc=g_doc_single,
        evaluation_date=today,
    )
    complete_eval = next(e for e in evals_single_valid if e.rule_code == "SEC_16_COMPLETE")
    assert complete_eval.eligibility_status == ITCEligibilityEnum.ELIGIBLE_EVIDENCE_COMPLETE
    assert complete_eval.risk_reversal_amount == Decimal("0.00")



