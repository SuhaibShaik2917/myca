"""
Statutory Reverse Charge Mechanism (RCM) & Goods Transport Agency (GTA) Validation Service.
Strictly compliant with:
- Section 9(3) / 9(4) of the CGST Act, 2017
- Section 5(3) of the IGST Act, 2017
- Notification No. 13/2017-Central Tax (Rate) Entry 1
- Notification No. 12/2017-Central Tax (Rate) Entry 18, 21A, 21B
- Notification No. 05/2022-Central Tax (Rate) (GTA forward-charge option & Annexure V)
- Section 12(8) of the IGST Act (Place of Supply for Goods Transportation)
- Section 49(4) of the CGST Act (RCM liability discharged only in cash via ECL)
"""
from decimal import Decimal
from typing import Dict, Any, Optional
from pydantic import BaseModel


class RCMValidationResult(BaseModel):
    is_rcm_applicable: bool
    tax_type: str  # INTRA_STATE, INTER_STATE, EXEMPT, FORWARD_CHARGE
    cgst_amount: Decimal = Decimal("0.00")
    sgst_amount: Decimal = Decimal("0.00")
    igst_amount: Decimal = Decimal("0.00")
    total_rcm_tax: Decimal = Decimal("0.00")
    is_cash_discharged: bool = False
    itc_eligibility_status: str  # ELIGIBLE_4A3, PENDING_CASH_DISCHARGE_31D, EXEMPT_NO_RCM, FORWARD_CHARGE_GTA
    statutory_basis: str
    explanation: str


# Specified categories of recipients liable under Section 9(3) Notification 13/2017-CT(R) Entry 1
GTA_SPECIFIED_RECIPIENT_CONSTITUTIONS = {
    "COMPANY", "BODY_CORPORATE", "REGISTERED_PERSON", "FACTORY",
    "SOCIETY", "COOPERATIVE_SOCIETY", "PARTNERSHIP_FIRM", "CASUAL_TAXABLE_PERSON"
}

# Indian PAN 4th character legal entity classification map
PAN_CONSTITUTION_MAP = {
    "C": "COMPANY",
    "P": "INDIVIDUAL_PROPRIETOR",
    "F": "PARTNERSHIP_FIRM_LLP",
    "G": "GOVERNMENT_AGENCY",
    "A": "ASSOCIATION_OF_PERSONS",
    "B": "BODY_OF_INDIVIDUALS",
    "H": "HINDU_UNDIVIDED_FAMILY",
    "L": "LOCAL_AUTHORITY",
    "J": "ARTIFICIAL_JURIDICAL_PERSON",
    "T": "TRUST"
}


def parse_pan_constitution(gstin: Optional[str]) -> str:
    """
    Parses the 4th character of the PAN embedded in a 15-digit GSTIN (index 5)
    to determine the legal constitution of the entity according to the Indian
    Income Tax Department and GST registration schema.
    """
    if not gstin or len(gstin.strip()) < 6:
        return "UNKNOWN"
    char = gstin.strip()[5].upper()
    return PAN_CONSTITUTION_MAP.get(char, "OTHER")


def validate_rcm_and_gta_eligibility(
    service_type: str,
    taxable_value: Decimal,
    gst_rate: Decimal,
    supplier_gstin: Optional[str],
    recipient_gstin: str,
    place_of_supply: str,
    has_consignment_note: bool = True,
    consignment_note_number: Optional[str] = None,
    gta_opted_forward_charge: bool = False,
    supplier_annexure_v_declaration: bool = False,
    recipient_constitution: str = "COMPANY",
    cash_payment_challan_ref: Optional[str] = None,
    cash_payment_date: Optional[str] = None
) -> RCMValidationResult:
    """
    Validates whether an inward transport or service invoice is subject to statutory RCM
    and whether corresponding Table 4(A)(3) ITC is available.
    """
    srv = (service_type or "").upper()
    taxable = Decimal(str(taxable_value or 0))
    rate = Decimal(str(gst_rate or 5.00))
    total_tax = (taxable * rate / Decimal("100.00")).quantize(Decimal("0.01"))

    # 1. Security Services (Supply of Security Personnel) under Notification No. 29/2018-CT(R) Entry 14:
    if srv in {"SECURITY_SERVICES", "SECURITY_PERSONNEL", "SAC_9985", "SAC_998529", "SECURITY"}:
        # Check supplier constitution from PAN 4th character:
        # A GSTIN is 15 chars: State(2) + PAN(10) + Entity(1) + 'Z' + Checksum(1)
        # 4th character of PAN is index 5 of GSTIN (supplier_gstin[5])
        supp_pan_4th = (supplier_gstin[5].upper() if supplier_gstin and len(supplier_gstin) >= 6 else "")
        is_body_corporate = supp_pan_4th in {"C", "U"}

        # Under Notification No. 29/2018-CT(R) Entry 14, RCM applies ONLY if supplier is "any person other than a body corporate"
        if is_body_corporate or supp_pan_4th == "C":
            return RCMValidationResult(
                is_rcm_applicable=False,
                tax_type="FORWARD_CHARGE",
                cgst_amount=Decimal("0.00"),
                sgst_amount=Decimal("0.00"),
                igst_amount=Decimal("0.00"),
                total_rcm_tax=Decimal("0.00"),
                is_cash_discharged=False,
                itc_eligibility_status="FORWARD_CHARGE_VENDOR_INVOICE",
                statutory_basis="Notification No. 29/2018-Central Tax (Rate) Entry 14",
                explanation=(
                    f"Supplier GSTIN {supplier_gstin} has PAN 4th character '{supp_pan_4th}', indicating a Body Corporate (Company). "
                    "Entry 14 of Notification 13/2017-CT(R) restricts security services RCM strictly to persons other than a body corporate. "
                    "The supply is under Forward Charge; recipient cannot discharge under RCM or include in Table 3.1(d). "
                    "Vendor must charge 18% GST on tax invoice reportable under Table 4(A)(5)."
                )
            )

        # Non-body corporate supplier: check recipient eligibility and 18% rate
        actual_rate = Decimal("18.00") if rate in {Decimal("5.00"), Decimal("10.00")} else rate
        actual_total_tax = (taxable * actual_rate / Decimal("100.00")).quantize(Decimal("0.01"))

        rec_state = recipient_gstin[:2] if len(recipient_gstin) >= 2 else place_of_supply
        supp_state = (supplier_gstin[:2] if supplier_gstin and len(supplier_gstin) >= 2 else rec_state)
        is_intra = (supp_state == place_of_supply)

        c = (actual_total_tax / Decimal("2.00")).quantize(Decimal("0.01")) if is_intra else Decimal("0.00")
        s = (actual_total_tax / Decimal("2.00")).quantize(Decimal("0.01")) if is_intra else Decimal("0.00")
        i = actual_total_tax if not is_intra else Decimal("0.00")

        cash_discharged = bool(cash_payment_challan_ref)
        status = "ELIGIBLE_4A3" if cash_discharged else "PENDING_CASH_DISCHARGE_31D"

        return RCMValidationResult(
            is_rcm_applicable=True,
            tax_type="INTRA_STATE" if is_intra else "INTER_STATE",
            cgst_amount=c,
            sgst_amount=s,
            igst_amount=i,
            total_rcm_tax=actual_total_tax,
            is_cash_discharged=cash_discharged,
            itc_eligibility_status=status,
            statutory_basis="Notification No. 29/2018-Central Tax (Rate) Entry 14",
            explanation=(
                f"Statutory RCM applies on Security Services from non-body corporate supplier (PAN 4th '{supp_pan_4th}'). "
                f"Tax rate: 18% (CGST ₹{c:,.2f}, SGST ₹{s:,.2f}, IGST ₹{i:,.2f}). "
                f"Must be discharged in cash via Table 3.1(d) before claiming Table 4(A)(3) ITC."
            )
        )

    # 2. Import of Services (Section 5(3) IGST Act / Notification 10/2017-IT(R)): always IGST
    if srv in ("IMPORT_SERVICES", "IMPORT_OF_SERVICES"):
        c = Decimal("0.00")
        s = Decimal("0.00")
        i = total_tax
        cash_discharged = bool(cash_payment_challan_ref)
        status = "ELIGIBLE_4A3" if cash_discharged else "PENDING_CASH_DISCHARGE_31D"
        return RCMValidationResult(
            is_rcm_applicable=True,
            tax_type="INTER_STATE",
            cgst_amount=c,
            sgst_amount=s,
            igst_amount=i,
            total_rcm_tax=total_tax,
            is_cash_discharged=cash_discharged,
            itc_eligibility_status=status,
            statutory_basis="Section 5(3) of IGST Act read with Notification No. 10/2017-Integrated Tax (Rate), Entry 1",
            explanation=(
                f"Import of services from supplier located outside India is taxable as INTER_STATE supply under IGST RCM "
                f"(IGST ₹{i:,.2f}). Mandatory cash discharge via Table 3.1(d) before claiming Table 4(A)(2)/(3) ITC."
            ),
        )

    # 3. Known Statutory Reverse Charge Service Categories (Section 9(3) / Notification 13/2017-CT(R))
    KNOWN_RCM_SERVICES = {
        "LEGAL_ADVOCATE", "ADVOCATE", "ARBITRAL_TRIBUNAL", "DIRECTOR_REMUNERATION", "SPONSORSHIP",
        "GOVERNMENT_LOCAL_AUTHORITY", "RECOVERY_AGENT", "AUTHOR_MUSIC_ARTIST", "OVERSEEING_COMMITTEE",
        "RENTING_MOTOR_VEHICLE", "SECURITY_SERVICES", "RESIDENTIAL_DWELLING",
    }
    if srv != "GTA_ROAD_TRANSPORT":
        if srv not in KNOWN_RCM_SERVICES:
            return RCMValidationResult(
                is_rcm_applicable=False,
                tax_type="FORWARD_CHARGE",
                cgst_amount=Decimal("0.00"),
                sgst_amount=Decimal("0.00"),
                igst_amount=Decimal("0.00"),
                total_rcm_tax=Decimal("0.00"),
                is_cash_discharged=False,
                itc_eligibility_status="NOT_APPLICABLE_FORWARD_CHARGE",
                statutory_basis="Section 9(1) of CGST Act / Notification No. 13/2017-CT(R) Schedule Exhaustive List",
                explanation=f"Service type '{srv}' does not fall under notified Section 9(3)/5(3) Reverse Charge categories."
            )
        rec_state = recipient_gstin[:2] if len(recipient_gstin) >= 2 else place_of_supply
        supp_state = (supplier_gstin[:2] if supplier_gstin and len(supplier_gstin) >= 2 else rec_state)
        is_intra = (supp_state == place_of_supply)

        c = (total_tax / Decimal("2.00")).quantize(Decimal("0.01")) if is_intra else Decimal("0.00")
        s = (total_tax / Decimal("2.00")).quantize(Decimal("0.01")) if is_intra else Decimal("0.00")
        i = total_tax if not is_intra else Decimal("0.00")

        cash_discharged = bool(cash_payment_challan_ref)
        status = "ELIGIBLE_4A3" if cash_discharged else "PENDING_CASH_DISCHARGE_31D"

        return RCMValidationResult(
            is_rcm_applicable=True,
            tax_type="INTRA_STATE" if is_intra else "INTER_STATE",
            cgst_amount=c,
            sgst_amount=s,
            igst_amount=i,
            total_rcm_tax=total_tax,
            is_cash_discharged=cash_discharged,
            itc_eligibility_status=status,
            statutory_basis="Section 9(3) of CGST Act / Section 5(3) of IGST Act read with Notification 13/2017-CT(R)",
            explanation=f"Statutory RCM applies on {srv}. Cash tax payment verification: {'Confirmed' if cash_discharged else 'Pending'}. Eligible under Table 4(A)(3) once paid in cash."
        )

    # 2. GTA Road Transport Validation:
    # A. Check Forward Charge Option (Notification 05/2022-CT(R)):
    if gta_opted_forward_charge or supplier_annexure_v_declaration:
        return RCMValidationResult(
            is_rcm_applicable=False,
            tax_type="FORWARD_CHARGE",
            cgst_amount=Decimal("0.00"),
            sgst_amount=Decimal("0.00"),
            igst_amount=Decimal("0.00"),
            total_rcm_tax=Decimal("0.00"),
            is_cash_discharged=False,
            itc_eligibility_status="FORWARD_CHARGE_GTA",
            statutory_basis="Notification No. 05/2022-Central Tax (Rate) amending Notification No. 11/2017-CT(R) and 13/2017-CT(R)",
            explanation="GTA supplier has exercised the statutory option to pay GST under forward charge (Annexure V declaration). Recipient is NOT liable under RCM. Tax must be charged on invoice by supplier and claimed under Table 4(A)(5)."
        )

    # B. Check Consignment Note Requirement:
    # Under GST law, goods transport by road without a consignment note is NOT a GTA service.
    # It is an exempt supply of transport of goods by road under Entry 18 of Notification 12/2017-CT(R).
    if not has_consignment_note or not (consignment_note_number and str(consignment_note_number).strip()):
        return RCMValidationResult(
            is_rcm_applicable=False,
            tax_type="EXEMPT",
            cgst_amount=Decimal("0.00"),
            sgst_amount=Decimal("0.00"),
            igst_amount=Decimal("0.00"),
            total_rcm_tax=Decimal("0.00"),
            is_cash_discharged=False,
            itc_eligibility_status="EXEMPT_NO_RCM",
            statutory_basis="Notification No. 12/2017-Central Tax (Rate), Entry 18 (Heading 9965/9967)",
            explanation="Transportation of goods by road without issuance of a consignment note does not constitute GTA service. It is an exempt supply; no reverse charge liability arises, and no ITC is available."
        )

    # C. Check Recipient Category:
    # Section 9(3) Notification 13/2017-CT(R) Entry 1 applies when the recipient belongs to specified categories.
    const = (recipient_constitution or "").upper()
    if const not in GTA_SPECIFIED_RECIPIENT_CONSTITUTIONS and not recipient_gstin:
        return RCMValidationResult(
            is_rcm_applicable=False,
            tax_type="EXEMPT",
            cgst_amount=Decimal("0.00"),
            sgst_amount=Decimal("0.00"),
            igst_amount=Decimal("0.00"),
            total_rcm_tax=Decimal("0.00"),
            is_cash_discharged=False,
            itc_eligibility_status="EXEMPT_NO_RCM",
            statutory_basis="Notification No. 12/2017-Central Tax (Rate), Entry 21A",
            explanation=f"Recipient constitution '{const}' is an unregistered individual/entity not falling within specified categories of Entry 1 Notification 13/2017-CT(R). Supply is exempt."
        )

    # D. Determine Place of Supply (Section 12(8) of IGST Act):
    # For transportation of goods to a registered person, PoS is the location of the registered recipient.
    rec_state = recipient_gstin[:2] if len(recipient_gstin) >= 2 else place_of_supply
    pos_state = place_of_supply if place_of_supply else rec_state

    # Origin / Supplier State
    supp_state = supplier_gstin[:2] if supplier_gstin and len(supplier_gstin) >= 2 else rec_state
    is_intra = (supp_state == pos_state)

    if is_intra:
        c = (total_tax / Decimal("2.00")).quantize(Decimal("0.01"))
        s = (total_tax / Decimal("2.00")).quantize(Decimal("0.01"))
        i = Decimal("0.00")
        tax_type = "INTRA_STATE"
    else:
        c = Decimal("0.00")
        s = Decimal("0.00")
        i = total_tax
        tax_type = "INTER_STATE"

    # E. Cash Tax Discharge Verification (Section 49(4)):
    # RCM liability cannot be set off against ITC; it MUST be paid in cash via GSTR-3B Table 3.1(d).
    cash_discharged = bool(cash_payment_challan_ref)
    itc_status = "ELIGIBLE_4A3" if cash_discharged else "PENDING_CASH_DISCHARGE_31D"

    return RCMValidationResult(
        is_rcm_applicable=True,
        tax_type=tax_type,
        cgst_amount=c,
        sgst_amount=s,
        igst_amount=i,
        total_rcm_tax=total_tax,
        is_cash_discharged=cash_discharged,
        itc_eligibility_status=itc_status,
        statutory_basis="Notification No. 13/2017-Central Tax (Rate), Entry 1 & Section 12(8) of IGST Act",
        explanation=(
            f"Statutory GTA reverse charge confirmed ({tax_type}). Consignment note verified. "
            f"Recipient is registered ({const}). Tax payable: CGST ₹{c:,.2f}, SGST ₹{s:,.2f}, IGST ₹{i:,.2f}. "
            f"Under Section 49(4), RCM liability must be discharged in cash via Table 3.1(d). "
            f"Table 4(A)(3) ITC Status: {'Eligible (Cash payment verified)' if cash_discharged else 'Pending cash discharge in Form GSTR-3B Table 3.1(d)'}."
        )
    )
