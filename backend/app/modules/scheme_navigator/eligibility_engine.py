from typing import Dict, Any, List, Tuple
from decimal import Decimal
from app.modules.scheme_navigator.schemas import EvaluatedCriterion

def evaluate_single_rule(
    rule_key: str,
    operator: str,
    target_value: str,
    profile_data: Dict[str, Any]
) -> Tuple[bool, str]:
    """
    Evaluates one deterministic statutory criterion against MSME profile attributes.
    Returns (passed: bool, actual_value_str: str).
    """
    actual = profile_data.get(rule_key)
    actual_str = str(actual) if actual is not None else ""

    op = operator.upper().strip()

    if op == "=":
        passed = actual_str.strip().lower() == target_value.strip().lower()
    elif op == "!=":
        passed = actual_str.strip().lower() != target_value.strip().lower()
    elif op == "IN":
        allowed_items = [item.strip().lower() for item in target_value.split(",")]
        passed = actual_str.strip().lower() in allowed_items
    elif op == "CONTAINS":
        passed = target_value.strip().lower() in actual_str.strip().lower()
    elif op in (">", ">=", "<", "<="):
        if actual is None or actual_str == "":
            return False, actual_str
        try:
            actual_num = Decimal(str(actual))
            target_num = Decimal(target_value)
            if op == ">":
                passed = actual_num > target_num
            elif op == ">=":
                passed = actual_num >= target_num
            elif op == "<":
                passed = actual_num < target_num
            elif op == "<=":
                passed = actual_num <= target_num
            else:
                passed = False
        except Exception:
            passed = False
    else:
        passed = False

    return passed, actual_str

def evaluate_scheme(
    rules: List[Dict[str, str]],
    profile_data: Dict[str, Any]
) -> Tuple[Decimal, List[EvaluatedCriterion]]:
    """
    Evaluates all rules for a scheme and computes match score (0.00 to 100.00).
    """
    if not rules:
        return Decimal("100.00"), []

    criteria_results: List[EvaluatedCriterion] = []
    passed_count = 0

    for r in rules:
        r_key = r["rule_key"]
        r_op = r["operator"]
        r_target = r["target_value"]
        r_desc = r.get("rule_description", f"{r_key} {r_op} {r_target}")

        passed, actual_val = evaluate_single_rule(r_key, r_op, r_target, profile_data)
        if passed:
            passed_count += 1

        criteria_results.append(EvaluatedCriterion(
            rule_key=r_key,
            description=r_desc,
            passed=passed,
            expected=f"{r_op} {r_target}",
            actual=actual_val or "Not Provided"
        ))

    score = (Decimal(passed_count) / Decimal(len(rules))) * Decimal("100.00")
    score = score.quantize(Decimal("0.01"))
    return score, criteria_results
