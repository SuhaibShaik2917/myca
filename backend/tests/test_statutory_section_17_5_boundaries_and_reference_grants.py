"""
Regression & Statutory Verification Test Suite:
1. Least-Privilege Reference Catalogue Write Denial under app_service_user
2. Section 17(5)(a) and Section 17(5)(ab) Vehicle Seating Threshold (13 vs 14 seats)
3. Goods Transport Vehicles, Passenger Transport Permits, and Ambiguous Facts
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
    and all INSERT, UPDATE, DELETE attempts are rejected at the database level.
    """
    db = SessionLocal()
    try:
        # A. SELECT is permitted
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
    - Commercial passenger transportation permit
    - Missing or unverified vehicle facts (routes to REVIEW_REQUIRED_FACTS_PENDING)
    - Business category label alone without permit evidence
    """
    # 1. Goods transport vehicle (HSN 8704 delivery truck)
    res_truck = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Delivery Truck",
        hsn_sac="8704",
        facts={"is_goods_transport_vehicle": True, "is_factual_context_verified": True}
    )
    assert res_truck["is_blocked"] is False
    assert res_truck["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"
    assert "Goods transport vehicle" in res_truck["statutory_citation"]

    # 2. 13-seater vehicle WITH verified commercial passenger transport license
    res_13_passenger = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Commercial Taxi 13-seater",
        hsn_sac="8702",
        facts={
            "seating_capacity": 13,
            "is_factual_context_verified": True,
            "passenger_transport_license": True
        }
    )
    assert res_13_passenger["is_blocked"] is False
    assert res_13_passenger["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"
    assert "Section 17(5)(a)(B)" in res_13_passenger["statutory_citation"]

    # 3. Taxpayer claims passenger transport business, but NO license/permit is verified
    res_claim_unsupported = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Taxi Service Expense",
        hsn_sac="8703",
        facts={
            "seating_capacity": 5,
            "is_factual_context_verified": True,
            "is_passenger_transport_business": True,
            "passenger_transport_license": False  # Missing permit!
        }
    )
    # Must NOT blindly infer eligibility from business-category label
    assert res_claim_unsupported["status"] == "REVIEW_REQUIRED_FACTS_PENDING"
    assert res_claim_unsupported["is_blocked"] is None

    # 4. Ambiguous / unverified vehicle facts (missing RC or registration details)
    res_unverified = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Vehicle Maintenance",
        hsn_sac="998729",
        facts={"is_factual_context_verified": False}
    )
    assert res_unverified["status"] == "REVIEW_REQUIRED_FACTS_PENDING"
    assert res_unverified["is_blocked"] is None


# ============================================================================
# 4. SECTION 17(5)(ab) VEHICLE REPAIRS & SERVICING
# ============================================================================

def test_section_17_5_ab_vehicle_repair_lineage():
    """
    Tests Section 17(5)(ab) repair services:
    - Repair of 14-seater vehicle: Eligible
    - Repair of 13-seater vehicle: Blocked
    - Repair of vehicle used in driving school: Eligible
    """
    # 1. Servicing of 14-seater vehicle (SAC 998729)
    res_repair_14 = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Bus Periodic Maintenance",
        hsn_sac="998729",
        facts={"seating_capacity": 14, "is_factual_context_verified": True}
    )
    assert res_repair_14["is_blocked"] is False
    assert res_repair_14["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"

    # 2. Servicing of 13-seater vehicle (SAC 998729)
    res_repair_13 = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Van Periodic Servicing",
        hsn_sac="998729",
        facts={"seating_capacity": 13, "is_factual_context_verified": True}
    )
    assert res_repair_13["is_blocked"] is True
    assert res_repair_13["status"] == "BLOCKED_CREDIT_CONFIRMED"

    # 3. Repair of driving school car (Section 17(5)(a)(C))
    res_school_repair = evaluate_section_17_5_vehicle_and_repair_eligibility(
        category_or_desc="Driving School Car Clutch Overhaul",
        hsn_sac="998729",
        facts={"seating_capacity": 5, "is_factual_context_verified": True, "is_driving_school_training": True}
    )
    assert res_school_repair["is_blocked"] is False
    assert res_school_repair["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"
