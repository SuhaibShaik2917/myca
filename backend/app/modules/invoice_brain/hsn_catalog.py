from typing import Dict, Optional, Tuple, NamedTuple
from decimal import Decimal

class HSNEntry(NamedTuple):
    code: str
    description: str
    category: str  # 'goods' or 'services'
    chapter: str
    gst_rate_default: Decimal
    effective_from: str
    source_reference: str

# Statutory Reference Catalogue based on CBIC Notification No. 78/2020 - Central Tax
# and GST Portal Table 12 Phase 3 Catalogue (May 2025 Advisory)
HSN_CATALOGUE: Dict[str, HSNEntry] = {
    # Services (SAC Chapter 99)
    "998311": HSNEntry("998311", "Management consulting and management services", "services", "99", Decimal("18.00"), "2017-07-01", "CBIC Scheme of Classification of Services"),
    "998312": HSNEntry("998312", "Business consulting services including public relations", "services", "99", Decimal("18.00"), "2017-07-01", "CBIC Scheme of Classification of Services"),
    "998313": HSNEntry("998313", "Information technology (IT) design and development services", "services", "99", Decimal("18.00"), "2017-07-01", "CBIC Scheme of Classification of Services"),
    "998314": HSNEntry("998314", "IT infrastructure provisioning and cloud hosting services", "services", "99", Decimal("18.00"), "2017-07-01", "CBIC Scheme of Classification of Services"),
    "998315": HSNEntry("998315", "IT infrastructure management and support services", "services", "99", Decimal("18.00"), "2017-07-01", "CBIC Scheme of Classification of Services"),
    "998211": HSNEntry("998211", "Legal advisory and representation services", "services", "99", Decimal("18.00"), "2017-07-01", "CBIC Scheme of Classification of Services"),
    "998221": HSNEntry("998221", "Accounting, auditing and bookkeeping services", "services", "99", Decimal("18.00"), "2017-07-01", "CBIC Scheme of Classification of Services"),
    "998231": HSNEntry("998231", "Tax consultancy and tax preparation services", "services", "99", Decimal("18.00"), "2017-07-01", "CBIC Scheme of Classification of Services"),
    "996511": HSNEntry("996511", "Road transport services of goods by freight forwarding / GTA", "services", "99", Decimal("5.00"), "2017-07-01", "CBIC Scheme of Classification of Services"),
    "997111": HSNEntry("997111", "Financial consultancy and banking agency services", "services", "99", Decimal("18.00"), "2017-07-01", "CBIC Scheme of Classification of Services"),
    "998711": HSNEntry("998711", "Maintenance and repair services of commercial computers and office equipment", "services", "99", Decimal("18.00"), "2017-07-01", "CBIC Scheme of Classification of Services"),

    # Goods (HSN Chapters 01-98)
    "8471": HSNEntry("8471", "Automatic data processing machines, computers and optical readers", "goods", "84", Decimal("18.00"), "2017-07-01", "First Schedule to the Customs Tariff Act, 1975"),
    "847130": HSNEntry("847130", "Portable automatic data processing machines, laptops and tablets", "goods", "84", Decimal("18.00"), "2017-07-01", "First Schedule to the Customs Tariff Act, 1975"),
    "84713010": HSNEntry("84713010", "Personal computers (laptops, notebook computers)", "goods", "84", Decimal("18.00"), "2017-07-01", "First Schedule to the Customs Tariff Act, 1975"),
    "847141": HSNEntry("847141", "Other automatic data processing machines comprising in the same housing a CPU", "goods", "84", Decimal("18.00"), "2017-07-01", "First Schedule to the Customs Tariff Act, 1975"),
    "8517": HSNEntry("8517", "Telephone sets, smartphones and apparatus for transmission of voice/data", "goods", "85", Decimal("18.00"), "2017-07-01", "First Schedule to the Customs Tariff Act, 1975"),
    "85171300": HSNEntry("85171300", "Smartphones for cellular networks", "goods", "85", Decimal("18.00"), "2021-01-01", "Finance Act 2021 Tariff Amendments"),
    "8528": HSNEntry("8528", "Monitors, projectors and television reception apparatus", "goods", "85", Decimal("18.00"), "2017-07-01", "First Schedule to the Customs Tariff Act, 1975"),
    "4820": HSNEntry("4820", "Registers, account books, notebooks, order books, receipt books", "goods", "48", Decimal("12.00"), "2017-07-01", "First Schedule to the Customs Tariff Act, 1975"),
    "7318": HSNEntry("7318", "Screws, bolts, nuts, coach screws, screw hooks, rivets, cotters", "goods", "73", Decimal("18.00"), "2017-07-01", "First Schedule to the Customs Tariff Act, 1975"),
    "8708": HSNEntry("8708", "Parts and accessories of the motor vehicles of headings 8701 to 8705", "goods", "87", Decimal("28.00"), "2017-07-01", "First Schedule to the Customs Tariff Act, 1975"),
}

def lookup_hsn_code(code: str) -> Optional[HSNEntry]:
    """Look up an HSN/SAC code directly or via its 4 or 6-digit prefix."""
    if not code:
        return None
    clean = str(code).strip()
    if clean in HSN_CATALOGUE:
        return HSN_CATALOGUE[clean]
    # Check 6-digit or 4-digit parent heading
    if len(clean) >= 6 and clean[:6] in HSN_CATALOGUE:
        return HSN_CATALOGUE[clean[:6]]
    if len(clean) >= 4 and clean[:4] in HSN_CATALOGUE:
        return HSN_CATALOGUE[clean[:4]]
    return None

def validate_hsn_compliance(
    code: Optional[str],
    aato_crores: Decimal = Decimal("5.0"),
    is_b2b: bool = True
) -> Tuple[bool, Optional[str], Optional[HSNEntry]]:
    """
    Validates HSN compliance based on CBIC Notification No. 78/2020 and GST Phase 3 rules:
    - Preceding year turnover (AATO) <= 5 Cr: Minimum 4 digits required for B2B (optional for B2C).
    - Preceding year turnover (AATO) > 5 Cr: Minimum 6 digits required for all supplies.
    - Code must resolve to a valid entry in the official GST Tariff Catalogue.
    Returns: (is_compliant, error_message, catalog_entry)
    """
    if not code:
        if is_b2b or aato_crores > Decimal("5.0"):
            return False, "HSN/SAC code is mandatory for this supply category", None
        return True, None, None  # Optional for small turnover B2C

    clean = str(code).strip()
    if not clean.isdigit():
        return False, f"HSN/SAC code '{code}' must contain numeric digits only", None

    # Check minimum required digits based on AATO
    if aato_crores > Decimal("5.0"):
        if len(clean) < 6:
            return False, f"Taxpayers with AATO > 5 Cr must report minimum 6 digits (provided: {len(clean)} digits)", None
    else:
        if is_b2b and len(clean) < 4:
            return False, f"Taxpayers with AATO <= 5 Cr must report minimum 4 digits for B2B (provided: {len(clean)} digits)", None

    entry = lookup_hsn_code(clean)
    if not entry:
        return False, f"HSN/SAC code '{clean}' is not recognized in official GST Tariff Catalogue", None

    return True, None, entry
