import re
from typing import Tuple

CATEGORY_RULES = [
    # Taxes and Statutory
    (r"(\bgst\b|goods\s*and\s*services|\btax\b|advance\s*tax|challan|\btds\b|cbic|commercial\s*tax)", "Taxes & Statutory"),
    # Debt Service & EMI
    (r"(\bemi\b|\bloan\b|mortgage|bajaj\s*fin|hdfc\s*bank\s*loan|icici\s*loan|sbi\s*loan|interest\s*dr|overdraft\s*int)", "Debt Service / EMI"),
    # Salaries & Payroll
    (r"(salary|payroll|wages|staff\s*payout|stipend|bonus|remuneration)", "Salary & Wages"),
    # Rent & Facility
    (r"(\brent\b|\blease\b|premises|office\s*maintenance|depot\s*rent|godown)", "Rent & Facility"),
    # Utilities & Telecom
    (r"(electricity|bescom|tangedco|mahavitaran|water\s*bill|broadband|airtel|jio|act\s*corp)", "Utilities & Telecom"),
    # Vendor / Procurement
    (r"(vendor|purchase|supplier|raw\s*material|packaging|steel|cement|chemical|textile|pvt\s*ltd|\bllp\b|enterprise|traders|\bcorp\b)", "Vendor / Procurement"),
    # Customer Revenue / Collections
    (r"(payment\s*received|collection|invoice\s*settlement|customer|pos\s*settle|qr\s*credit|upi\s*inflow|neft\s*cr|rtgs\s*cr)", "Customer Revenue"),
    # Logistics & Freight
    (r"(freight|transport|logistics|courier|delhivery|blue\s*dart|vrl|tci|cargo)", "Logistics & Freight"),
]

def categorize_transaction(description: str, tx_type: str) -> str:
    """
    Deterministically classifies a banking or UPI transaction into standard business categories.
    """
    if not description:
        return "Customer Revenue" if tx_type == "credit" else "General Operations"

    desc_lower = description.lower()
    for pattern, category in CATEGORY_RULES:
        if re.search(pattern, desc_lower):
            return category

    if tx_type == "credit":
        return "Customer Revenue"
    return "General Operations"
