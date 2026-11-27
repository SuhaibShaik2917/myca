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
    """Checks global government_schemes catalog; seeds only under superuser/admin migration context if empty."""
    count = session.execute(text("SELECT COUNT(*) FROM public.government_schemes;")).scalar() or 0
    if count > 0:
        return

    db_role = session.execute(text("SELECT current_user;")).scalar()
    if db_role == "app_service_user":
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
            )
            ON CONFLICT (scheme_code) DO NOTHING;
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
    """Retrieves all active government schemes with their eligibility rules (batch-loaded, no N+1)."""
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
    if not rows:
        return []

    scheme_ids = [str(r.id) for r in rows]
    rule_rows = session.execute(
        text("""
        SELECT scheme_id::text as scheme_id, rule_key, operator, target_value, rule_description
        FROM public.scheme_eligibility_rules
        WHERE scheme_id::text = ANY(:sids);
        """),
        {"sids": scheme_ids},
    ).fetchall()
    rules_by_scheme: Dict[str, List[SchemeRuleOut]] = {}
    for rr in rule_rows:
        rules_by_scheme.setdefault(rr.scheme_id, []).append(
            SchemeRuleOut(
                rule_key=rr.rule_key,
                operator=rr.operator,
                target_value=rr.target_value,
                rule_description=rr.rule_description,
            )
        )

    return [
        GovernmentSchemeOut(
            id=str(r.id),
            scheme_code=r.scheme_code,
            name=r.name,
            ministry=r.ministry,
            category=SchemeCategory(r.category),
            description=r.description,
            benefits_summary=r.benefits_summary,
            max_subsidy_amount=r.max_subsidy_amount,
            is_active=r.is_active,
            rules=rules_by_scheme.get(str(r.id), []),
        )
        for r in rows
    ]

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
               annual_turnover, credit_score, udyam_number, gstin,
               vintage_years, social_category, has_udyam
        FROM public.business_profiles
        WHERE org_id = :org
        ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
        LIMIT 1;
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
            gstin=row.gstin,
            vintage_years=row.vintage_years,
            social_category=row.social_category,
            has_udyam=row.has_udyam if row.has_udyam is not None else bool(row.udyam_number),
        )

    # If not exists, create a default profile
    new_id = str(uuid.uuid4())
    session.execute(
        text("""
        INSERT INTO public.business_profiles (
            id, org_id, business_type, industry_sector, turnover_bracket, annual_turnover,
            vintage_years, social_category, has_udyam
        ) VALUES (
            :id, :org, 'Proprietorship', 'Manufacturing', 'Micro (< 5 Cr)', 0.00,
            NULL, 'General', false
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
        annual_turnover=Decimal("0.00"),
        vintage_years=None,
        social_category="General",
        has_udyam=False,
    )

def upsert_business_profile(
    session: Session,
    org_id: str,
    user_id: Optional[str],
    profile_in: BusinessProfileIn
) -> BusinessProfileOut:
    """Updates or creates MSME profile persisting vintage_years, social_category, and has_udyam."""
    set_tenant_context(session, org_id=org_id, user_id=user_id)
    has_udyam_val = bool(profile_in.udyam_number) if profile_in.udyam_number else bool(profile_in.has_udyam)

    existing = session.execute(
        text("""
        SELECT id FROM public.business_profiles
        WHERE org_id = :org
        ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
        LIMIT 1;
        """),
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
                vintage_years = :vintage,
                social_category = :social,
                has_udyam = :has_udyam,
                updated_at = clock_timestamp()
            WHERE id = :pid AND org_id = :org;
            """),
            {
                "pid": existing.id,
                "btype": profile_in.business_type,
                "sector": profile_in.industry_sector,
                "bracket": profile_in.turnover_bracket,
                "turnover": profile_in.annual_turnover,
                "udyam": profile_in.udyam_number,
                "vintage": profile_in.vintage_years,
                "social": profile_in.social_category,
                "has_udyam": has_udyam_val,
                "org": org_id
            }
        )
    else:
        new_id = str(uuid.uuid4())
        session.execute(
            text("""
            INSERT INTO public.business_profiles (
                id, org_id, business_type, industry_sector, turnover_bracket,
                annual_turnover, udyam_number, vintage_years, social_category, has_udyam
            ) VALUES (
                :id, :org, :btype, :sector, :bracket, :turnover, :udyam,
                :vintage, :social, :has_udyam
            );
            """),
            {
                "id": new_id,
                "org": org_id,
                "btype": profile_in.business_type,
                "sector": profile_in.industry_sector,
                "bracket": profile_in.turnover_bracket,
                "turnover": profile_in.annual_turnover,
                "udyam": profile_in.udyam_number,
                "vintage": profile_in.vintage_years,
                "social": profile_in.social_category,
                "has_udyam": has_udyam_val,
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

    # 1. Profile data resolution from persisted DB values (never overwritten with fake constants)
    if profile_override:
        profile_dict = profile_override.model_dump()
        upsert_business_profile(session, org_id, user_id, profile_override)
    else:
        existing_profile = get_or_create_business_profile(session, org_id, user_id)
        profile_dict = {
            "business_type": existing_profile.business_type,
            "industry_sector": existing_profile.industry_sector,
            "turnover_bracket": existing_profile.turnover_bracket or "Micro (< 5 Cr)",
            "annual_turnover": existing_profile.annual_turnover,
            "udyam_number": existing_profile.udyam_number,
            "vintage_years": existing_profile.vintage_years,
            "social_category": existing_profile.social_category,
            "has_udyam": existing_profile.has_udyam if existing_profile.has_udyam is not None else bool(existing_profile.udyam_number)
        }

    # Ensure has_udyam is evaluated accurately
    if profile_dict.get("udyam_number"):
        profile_dict["has_udyam"] = True
    elif "has_udyam" not in profile_dict or profile_dict["has_udyam"] is None:
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
