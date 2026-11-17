import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple, Dict, Any
from app.schemas.invoice_brain import ValidationReport, ValidationFlag
from app.modules.invoice_brain.hsn_catalog import validate_hsn_compliance, lookup_hsn_code

CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

def calculate_gstin_checksum(gstin_14: str) -> str:
    """
    Computes 15th checksum character for a 14-character GSTIN base string
    using Indian GST Modulo-36 algorithm.
    """
    factor = 1
    total = 0
    for char in gstin_14[:14]:
        code = CHARS.index(char)
        product = code * (1 if factor % 2 == 1 else 2)
        factor += 1
        digit_sum = (product // 36) + (product % 36)
        total += digit_sum
    remainder = total % 36
    check_code = (36 - remainder) % 36
    return CHARS[check_code]

# Valid Indian State / UT Codes for GSTIN
VALID_STATE_CODES = {
    "01", "02", "03", "04", "05", "06", "07", "08", "09", "10",
    "11", "12", "13", "14", "15", "16", "17", "18", "19", "20",
    "21", "22", "23", "24", "25", "26", "27", "28", "29", "30",
    "31", "32", "33", "34", "35", "36", "37", "97", "99"
}

# Standard reference sample of common HSN/SAC codes
COMMON_HSN_SAC_PREFIXES = {
    # Services (SAC)
    "9983",  # IT, consulting, architecture
    "9982",  # Legal, accounting
    "9984",  # Telecommunications, information
    "9985",  # Support, security, cleaning
    "9987",  # Maintenance, repair
    "9971",  # Financial and insurance
    "9973",  # Leasing, licensing
    "9965",  # Goods transport
    "9967",  # Supporting transport
    # Goods (HSN)
    "8471",  # Computers, data processing
    "8517",  # Telephones, networking
    "8528",  # Monitors, projectors
    "4820",  # Paper stationery, registers
    "3926",  # Plastic articles
    "7318",  # Screws, bolts, hardware
    "8708",  # Motor vehicle parts
}

def validate_gstin_format_and_checksum(gstin: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Validates Indian GSTIN format and 15th character checksum using Modulo 36 algorithm.
    Returns: (is_valid, error_message)
    """
    if not gstin:
        return False, "GSTIN is missing"

    gstin = gstin.strip().upper()
    if len(gstin) != 15:
        return False, f"GSTIN must be 15 characters, got {len(gstin)}"

    # Regex check: 2 digits state + 5 char PAN + 4 digits + 1 char PAN entity + 1 entity code + 'Z' + 1 checksum
    pattern = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
    if not re.match(pattern, gstin):
        return False, "GSTIN does not match official format (e.g. 27AABCB1234M1Z2)"

    state_code = gstin[:2]
    if state_code not in VALID_STATE_CODES:
        return False, f"Invalid GST state code: {state_code}"

    # Modulo 36 Checksum verification
    try:
        factor = 1
        total = 0
        for char in gstin[:-1]:
            code = CHARS.index(char)
            product = code * (1 if factor % 2 == 1 else 2)
            factor += 1
            digit_sum = (product // 36) + (product % 36)
            total += digit_sum
        remainder = total % 36
        expected_check_code = (36 - remainder) % 36
        expected_char = CHARS[expected_check_code]

        if gstin[-1] != expected_char:
            return False, f"GSTIN checksum mismatch (expected {expected_char}, got {gstin[-1]})"
    except Exception as e:
        return False, f"Checksum calculation failed: {e}"

    return True, None

def validate_hsn_sac(code: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Validates HSN/SAC code against Indian GST nomenclature:
    - 4, 6, or 8 digits for Goods (HSN)
    - 6 digits starting with 99 for Services (SAC)
    """
    if not code:
        return False, "HSN/SAC code is missing"

    clean_code = str(code).strip()
    if not clean_code.isdigit():
        return False, "HSN/SAC code must contain only numeric digits"

    code_len = len(clean_code)
    if code_len not in (4, 6, 8):
        return False, f"HSN/SAC code must be 4, 6, or 8 digits, got {code_len}"

    if clean_code.startswith("99"):
        if code_len != 6:
            return False, f"Service Accounting Code (SAC) must be 6 digits starting with 99, got {code_len}"
    else:
        # Check against common prefixes
        prefix = clean_code[:4]
        if prefix not in COMMON_HSN_SAC_PREFIXES and code_len not in (6, 8):
            return True, None  # Non-standard but acceptable length

    return True, None

def validate_invoice_financials(
    invoice: Dict[str, Any],
    line_items: List[Dict[str, Any]],
    supplier_gstin: Optional[str] = None,
    aato_crores: Optional[Decimal] = Decimal("5.0")
) -> ValidationReport:
    """
    Performs comprehensive deterministic validation of invoice data:
    1. GSTIN validation and counterparty verification.
    2. Line item math and rounding checks.
    3. Missing field detection (no fabricated defaults).
    4. Intra-state vs Inter-state tax consistency.
    5. Grand total aggregation.
    6. HSN/SAC code validation.
    7. ITC eligibility risk flags.
    """
    flags: List[ValidationFlag] = []
    has_critical = False

    # 1. Counterparty GSTIN Verification
    cp_gstin = invoice.get("counterparty_gstin")
    gstin_valid = False
    if not cp_gstin:
        flags.append(ValidationFlag(
            code="ERR_MISSING_GSTIN",
            severity="warning",
            field="counterparty_gstin",
            message="Counterparty GSTIN is missing; invoice will be treated as B2C (ITC ineligible for business buyer)."
        ))
    else:
        ok, err_msg = validate_gstin_format_and_checksum(cp_gstin)
        if not ok:
            flags.append(ValidationFlag(
                code="ERR_INVALID_GSTIN",
                severity="error",
                field="counterparty_gstin",
                message=f"Counterparty GSTIN invalid: {err_msg}"
            ))
            has_critical = True
        else:
            gstin_valid = True

    # 2. Date Verification
    inv_date = invoice.get("invoice_date")
    if inv_date:
        if isinstance(inv_date, str):
            try:
                inv_date = date.fromisoformat(inv_date)
            except ValueError:
                inv_date = None

        if inv_date:
            today = date.today()
            if inv_date > today:
                flags.append(ValidationFlag(
                    code="ERR_FUTURE_DATE",
                    severity="error",
                    field="invoice_date",
                    message=f"Invoice date {inv_date} is in the future."
                ))
                has_critical = True
            elif (today - inv_date).days > 365:
                flags.append(ValidationFlag(
                    code="WARN_DATE_STALE",
                    severity="warning",
                    field="invoice_date",
                    message=f"Invoice is older than 365 days ({inv_date}); ITC claim time limits may have lapsed."
                ))

    # 3. Line Items Validation
    if not line_items:
        flags.append(ValidationFlag(
            code="ERR_NO_LINE_ITEMS",
            severity="error",
            field="line_items",
            message="Invoice contains zero line items."
        ))
        has_critical = True

    sum_taxable = Decimal("0.00")
    sum_cgst = Decimal("0.00")
    sum_sgst = Decimal("0.00")
    sum_igst = Decimal("0.00")
    sum_total = Decimal("0.00")
    all_hsn_valid = True
    has_missing_line_fields = False

    # Determine Place of Supply logic (Interstate vs Intrastate)
    is_interstate = False
    if supplier_gstin and cp_gstin and len(supplier_gstin) >= 2 and len(cp_gstin) >= 2:
        if supplier_gstin[:2] != cp_gstin[:2]:
            is_interstate = True

    for idx, item in enumerate(line_items):
        item_prefix = f"line_items[{idx}]"
        
        # Check for genuine missing extraction fields (no fabricated defaults)
        raw_qty = item.get("quantity")
        raw_price = item.get("unit_price")
        raw_taxable = item.get("taxable_amount")
        raw_rate = item.get("gst_rate")
        raw_tot = item.get("total_amount")

        missing_fields = []
        if raw_qty is None:
            missing_fields.append("quantity")
        if raw_price is None:
            missing_fields.append("unit_price")
        if raw_taxable is None:
            missing_fields.append("taxable_amount")
        if raw_rate is None:
            missing_fields.append("gst_rate")
        if raw_tot is None:
            missing_fields.append("total_amount")

        if missing_fields:
            has_missing_line_fields = True
            has_critical = True
            for f in missing_fields:
                flags.append(ValidationFlag(
                    code=f"ERR_MISSING_{f.upper()}",
                    severity="error",
                    field=f"{item_prefix}.{f}",
                    message=f"Line item {idx+1} is missing mandatory field {f} from OCR extraction"
                ))
            continue

        qty = Decimal(str(raw_qty))
        price = Decimal(str(raw_price))
        taxable = Decimal(str(raw_taxable))
        rate = Decimal(str(raw_rate))
        cgst = Decimal(str(item.get("cgst_amount") if item.get("cgst_amount") is not None else 0))
        sgst = Decimal(str(item.get("sgst_amount") if item.get("sgst_amount") is not None else 0))
        igst = Decimal(str(item.get("igst_amount") if item.get("igst_amount") is not None else 0))
        tot = Decimal(str(raw_tot))

        # Math check: taxable == qty * price
        expected_taxable = (qty * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if abs(taxable - expected_taxable) > Decimal("0.10"):
            flags.append(ValidationFlag(
                code="ERR_LINE_ARITHMETIC",
                severity="error",
                field=f"{item_prefix}.taxable_amount",
                message=f"Line {idx+1} taxable amount {taxable} does not match qty * price ({expected_taxable})"
            ))
            has_critical = True

        # Tax calculation check
        expected_total_tax = (taxable * (rate / Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        actual_total_tax = cgst + sgst + igst
        if abs(actual_total_tax - expected_total_tax) > Decimal("0.50"):
            flags.append(ValidationFlag(
                code="ERR_LINE_TAX_MISMATCH",
                severity="error",
                field=f"{item_prefix}.tax_amount",
                message=f"Line {idx+1} calculated tax ({actual_total_tax}) does not match {rate}% of taxable ({expected_total_tax})"
            ))
            has_critical = True

        # Interstate vs Intrastate check
        if is_interstate:
            if (cgst > 0 or sgst > 0) and igst == 0:
                flags.append(ValidationFlag(
                    code="ERR_INTERSTATE_CGST_SGST",
                    severity="error",
                    field=f"{item_prefix}.tax_type",
                    message=f"Line {idx+1}: Interstate transaction between states {supplier_gstin[:2]} and {cp_gstin[:2]} must use IGST, not CGST/SGST."
                ))
                has_critical = True
        else:
            if igst > 0 and (cgst == 0 and sgst == 0) and supplier_gstin and cp_gstin:
                flags.append(ValidationFlag(
                    code="ERR_INTRASTATE_IGST",
                    severity="error",
                    field=f"{item_prefix}.tax_type",
                    message=f"Line {idx+1}: Intrastate transaction within state {supplier_gstin[:2]} must use CGST + SGST, not IGST."
                ))
                has_critical = True

        # HSN/SAC statutory compliance check (Notification No. 78/2020 & Phase 3 rules)
        hsn = item.get("hsn_sac_code")
        hsn_res = validate_hsn_compliance(hsn, aato_crores=aato_crores, is_b2b=bool(cp_gstin))
        if not hsn_res.is_compliant:
            sev = "error" if hsn_res.classification == "aato_unverified_unresolved" else "warning"
            flags.append(ValidationFlag(
                code="WARN_INVALID_HSN" if sev == "warning" else "ERR_INVALID_HSN",
                severity=sev,
                field=f"{item_prefix}.hsn_sac_code",
                message=f"Line {idx+1} HSN compliance issue: {hsn_res.error_message}"
            ))
            all_hsn_valid = False
            if sev == "error":
                has_critical = True

        sum_taxable += taxable
        sum_cgst += cgst
        sum_sgst += sgst
        sum_igst += igst
        sum_total += tot

    # 4. Total Consistency Checks
    inv_taxable = Decimal(str(invoice.get("total_taxable_amount", 0)))
    inv_cgst = Decimal(str(invoice.get("cgst_amount", 0)))
    inv_sgst = Decimal(str(invoice.get("sgst_amount", 0)))
    inv_igst = Decimal(str(invoice.get("igst_amount", 0)))
    inv_total = Decimal(str(invoice.get("total_amount", 0)))

    arithmetic_valid = not has_missing_line_fields
    if abs(sum_taxable - inv_taxable) > Decimal("0.50"):
        flags.append(ValidationFlag(
            code="ERR_SUM_TAXABLE_MISMATCH",
            severity="error",
            field="total_taxable_amount",
            message=f"Invoice total taxable amount ({inv_taxable}) does not match sum of lines ({sum_taxable})"
        ))
        has_critical = True
        arithmetic_valid = False

    expected_grand_total = inv_taxable + inv_cgst + inv_sgst + inv_igst
    if abs(expected_grand_total - inv_total) > Decimal("1.00"):
        flags.append(ValidationFlag(
            code="ERR_GRAND_TOTAL_MISMATCH",
            severity="error",
            field="total_amount",
            message=f"Invoice total amount ({inv_total}) does not equal taxable + taxes ({expected_grand_total})"
        ))
        has_critical = True
        arithmetic_valid = False

    itc_eligible = gstin_valid and arithmetic_valid and not has_critical

    return ValidationReport(
        is_valid=not has_critical,
        has_critical_errors=has_critical,
        arithmetic_valid=arithmetic_valid,
        gstin_valid=gstin_valid,
        hsn_valid=all_hsn_valid,
        itc_eligible=itc_eligible,
        flags=flags,
        discrepancy_count=len(flags)
    )
