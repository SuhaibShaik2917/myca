import json
import uuid
from decimal import Decimal
from typing import List, Dict, Any, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import set_tenant_context
from app.modules.scheme_navigator.schemas import (
    GovernmentSchemeOut, SchemeRuleOut, BusinessProfileIn,
    BusinessProfileOut, SchemeMatchOut, SchemeChecklistOut,
    EvaluatedCriterion, SchemeCategory
)
from app.modules.scheme_navigator.schemes_catalog import SEED_SCHEMES
from app.modules.scheme_navigator.eligibility_engine import evaluate_scheme

def seed_schemes_catalog_if_needed(session: Session) -> None:
    """Seeds the global government_schemes catalog if table is empty."""
    count = session.execute(text("SELECT COUNT(*) FROM public.government_schemes;")).scalar() or 0
    if count > 0:
        return

    for s in SEED_SCHEMES:
        scheme_id = str(uuid.uuid4())
        session.execute(
            text("""
            INSERT INTO public.government_schemes (
                id, scheme_code, name, ministry, category,
                description, benefits_summary, max_subsidy_amount, is_active
            ) VALUES (
                :id, :code, :name, :min, CAST(:cat AS public.scheme_category),
                :desc, :benefits, :max_sub, true
            );
            """),
            {
                "id": scheme_id,
                "code": s["scheme_code"],
                "name": s["name"],
                "min": s["ministry"],
                "cat": s["category"],
                "desc": s["description"],
                "benefits": s["benefits_summary"],
                "max_sub": s.get("max_subsidy_amount")
            }
        )

        for r in s["rules"]:
            rule_id = str(uuid.uuid4())
            session.execute(
                text("""
                INSERT INTO public.scheme_eligibility_rules (
                    id, scheme_id, rule_key, operator, target_value, rule_description
                ) VALUES (
                    :id, :sid, :key, :op, :val, :desc
                );
                """),
                {
                    "id": rule_id,
                    "sid": scheme_id,
                    "key": r["rule_key"],
                    "op": r["operator"],
                    "val": r["target_value"],
                    "desc": r["rule_description"]
                }
            )

    session.commit()

def list_government_schemes(session: Session, category: Optional[str] = None) -> List[GovernmentSchemeOut]:
    """Retrieves all active government schemes with their eligibility rules."""
    seed_schemes_catalog_if_needed(session)

    query = """
    SELECT id, scheme_code, name, ministry, category::text as category,
           description, benefits_summary, max_subsidy_amount, is_active
    FROM public.government_schemes
    WHERE is_active = true
    """
    params = {}
    if category:
        query += " AND category = CAST(:cat AS public.scheme_category)"
        params["cat"] = category
    query += " ORDER BY name ASC;"

    rows = session.execute(text(query), params).fetchall()
    schemes: List[GovernmentSchemeOut] = []

    for r in rows:
        rule_rows = session.execute(
            text("""
            SELECT rule_key, operator, target_value, rule_description
            FROM public.scheme_eligibility_rules
            WHERE scheme_id = :sid;
            """),
            {"sid": str(r.id)}
        ).fetchall()

        rules = [
            SchemeRuleOut(
                rule_key=rr.rule_key,
                operator=rr.operator,
                target_value=rr.target_value,
                rule_description=rr.rule_description
            ) for rr in rule_rows
        ]

        schemes.append(GovernmentSchemeOut(
            id=str(r.id),
            scheme_code=r.scheme_code,
            name=r.name,
            ministry=r.ministry,
            category=SchemeCategory(r.category),
            description=r.description,
            benefits_summary=r.benefits_summary,
            max_subsidy_amount=r.max_subsidy_amount,
            is_active=r.is_active,
            rules=rules
        ))

    return schemes

def get_or_create_business_profile(
    session: Session,
    org_id: str,
    user_id: Optional[str] = None
) -> BusinessProfileOut:
    """Fetches business profile for the organization."""
    set_tenant_context(session, org_id=org_id, user_id=user_id)

    row = session.execute(
        text("""
        SELECT id, org_id, business_type, industry_sector, turnover_bracket,
               annual_turnover, credit_score, udyam_number, gstin
        FROM public.business_profiles
        WHERE org_id = :org;
        """),
        {"org": org_id}
    ).fetchone()

    if row:
        return BusinessProfileOut(
            id=str(row.id),
            org_id=str(row.org_id),
            business_type=row.business_type,
            industry_sector=row.industry_sector,
            turnover_bracket=row.turnover_bracket,
            annual_turnover=row.annual_turnover,
            credit_score=row.credit_score,
            udyam_number=row.udyam_number,
            gstin=row.gstin
        )

    # If not exists, create a default profile
    new_id = str(uuid.uuid4())
    session.execute(
        text("""
        INSERT INTO public.business_profiles (
            id, org_id, business_type, industry_sector, turnover_bracket, annual_turnover
        ) VALUES (
            :id, :org, 'Proprietorship', 'Manufacturing', 'Micro (< 5 Cr)', 0.00
        );
        """),
        {"id": new_id, "org": org_id}
    )
    session.commit()

    return BusinessProfileOut(
        id=new_id,
        org_id=org_id,
        business_type="Proprietorship",
        industry_sector="Manufacturing",
        turnover_bracket="Micro (< 5 Cr)",
        annual_turnover=Decimal("0.00")
    )

def upsert_business_profile(
    session: Session,
    org_id: str,
    user_id: Optional[str],
    profile_in: BusinessProfileIn
) -> BusinessProfileOut:
    """Updates or creates MSME profile."""
    set_tenant_context(session, org_id=org_id, user_id=user_id)

    existing = session.execute(
        text("SELECT id FROM public.business_profiles WHERE org_id = :org;"),
        {"org": org_id}
    ).fetchone()

    if existing:
        session.execute(
            text("""
            UPDATE public.business_profiles
            SET business_type = :btype,
                industry_sector = :sector,
                turnover_bracket = :bracket,
                annual_turnover = :turnover,
                udyam_number = :udyam,
                updated_at = clock_timestamp()
            WHERE org_id = :org;
            """),
            {
                "btype": profile_in.business_type,
                "sector": profile_in.industry_sector,
                "bracket": profile_in.turnover_bracket,
                "turnover": profile_in.annual_turnover,
                "udyam": profile_in.udyam_number,
                "org": org_id
            }
        )
    else:
        new_id = str(uuid.uuid4())
        session.execute(
            text("""
            INSERT INTO public.business_profiles (
                id, org_id, business_type, industry_sector, turnover_bracket,
                annual_turnover, udyam_number
            ) VALUES (
                :id, :org, :btype, :sector, :bracket, :turnover, :udyam
            );
            """),
            {
                "id": new_id,
                "org": org_id,
                "btype": profile_in.business_type,
                "sector": profile_in.industry_sector,
                "bracket": profile_in.turnover_bracket,
                "turnover": profile_in.annual_turnover,
                "udyam": profile_in.udyam_number
            }
        )

    session.commit()
    return get_or_create_business_profile(session, org_id, user_id)

def evaluate_and_persist_matches(
    session: Session,
    org_id: str,
    user_id: Optional[str] = None,
    profile_override: Optional[BusinessProfileIn] = None
) -> List[SchemeMatchOut]:
    """
    Evaluates enterprise against all schemes and saves match results into scheme_matches.
    """
    set_tenant_context(session, org_id=org_id, user_id=user_id)
    seed_schemes_catalog_if_needed(session)

    # 1. Profile data resolution
    if profile_override:
        profile_dict = profile_override.model_dump()
        upsert_business_profile(session, org_id, user_id, profile_override)
    else:
        existing_profile = get_or_create_business_profile(session, org_id, user_id)
        profile_dict = {
            "business_type": existing_profile.business_type,
            "industry_sector": existing_profile.industry_sector,
            "turnover_bracket": existing_profile.turnover_bracket or "Micro (< 5 Cr)",
            "annual_turnover": existing_profile.annual_turnover or Decimal("0.00"),
            "udyam_number": existing_profile.udyam_number,
            "vintage_years": 3,
            "social_category": "General",
            "has_udyam": bool(existing_profile.udyam_number)
        }

    # Ensure has_udyam is evaluated accurately
    if profile_dict.get("udyam_number"):
        profile_dict["has_udyam"] = True
    elif "has_udyam" not in profile_dict:
        profile_dict["has_udyam"] = False

    # 2. Get all schemes and rules
    schemes = list_government_schemes(session)
    matches: List[SchemeMatchOut] = []

    for s in schemes:
        rules_list = [
            {
                "rule_key": r.rule_key,
                "operator": r.operator,
                "target_value": r.target_value,
                "rule_description": r.rule_description
            } for r in s.rules
        ]

        score, criteria = evaluate_scheme(rules_list, profile_dict)

        if score >= Decimal("75.00"):
            status = "recommended"
        elif score >= Decimal("50.00"):
            status = "applied"  # Partially matched / eligible with condition
        else:
            status = "ineligible"

        criteria_json = json.dumps([c.model_dump() for c in criteria])

        # Upsert into scheme_matches table
        session.execute(
            text("""
            INSERT INTO public.scheme_matches (
                id, org_id, scheme_id, match_score, status, evaluated_criteria, updated_at
            ) VALUES (
                gen_random_uuid(), :org, :sid, :score, :status, CAST(:crit AS jsonb), clock_timestamp()
            )
            ON CONFLICT (org_id, scheme_id)
            DO UPDATE SET
                match_score = EXCLUDED.match_score,
                status = EXCLUDED.status,
                evaluated_criteria = EXCLUDED.evaluated_criteria,
                updated_at = clock_timestamp();
            """),
            {
                "org": org_id,
                "sid": s.id,
                "score": score,
                "status": status,
                "crit": criteria_json
            }
        )

        matches.append(SchemeMatchOut(
            org_id=org_id,
            scheme_id=s.id,
            scheme_code=s.scheme_code,
            scheme_name=s.name,
            ministry=s.ministry,
            category=s.category,
            match_score=score,
            status=status,
            evaluated_criteria=criteria,
            benefits_summary=s.benefits_summary,
            max_subsidy_amount=s.max_subsidy_amount
        ))

    session.commit()
    # Sort highest match score first
    matches.sort(key=lambda x: x.match_score, reverse=True)
    return matches

def get_checklist_for_scheme(scheme_code: str) -> Optional[SchemeChecklistOut]:
    """Returns official required documents and step-by-step application instructions."""
    code_upper = scheme_code.upper().strip()
    for s in SEED_SCHEMES:
        if s["scheme_code"] == code_upper:
            chk = s["checklist"]
            return SchemeChecklistOut(
                scheme_code=s["scheme_code"],
                name=s["name"],
                ministry=s["ministry"],
                category=SchemeCategory(s["category"]),
                eligibility_summary=chk["eligibility_summary"],
                required_documents=chk["required_documents"],
                application_steps=chk["application_steps"],
                portal_url=chk["portal_url"],
                official_guidelines_url=chk["official_guidelines_url"]
            )
    return None
