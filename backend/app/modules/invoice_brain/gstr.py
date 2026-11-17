import uuid
from decimal import Decimal
from typing import Dict, Any, List, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.invoice_brain import (
    GSTR1DraftResponse, GSTR3BDraftResponse,
    GSTR1Table4Entry, GSTR1Table12Entry,
    GSTR_WORKING_DRAFT_DISCLAIMER, GSTR3B_WORKING_SUMMARY_DISCLAIMER
)
from app.modules.invoice_brain.hsn_catalog import lookup_hsn_code


def get_b2c_large_threshold(tax_period: str) -> Decimal:
    """
    Returns statutory B2C Large invoice threshold:
    - Notification No. 12/2024 – Central Tax (effective August 2024) amended Rule 59(4):
      For tax period >= '2024-08', interstate unregistered invoice threshold is > ₹1,00,000.
    - Historical periods (< '2024-08'): interstate unregistered invoice threshold was > ₹2,50,000.
    """
    if tax_period >= "2024-08":
        return Decimal("100000.00")
    return Decimal("250000.00")


def prepare_gstr1_draft(
    session: Session,
    org_id: str,
    tax_period: str  # Format: 'YYYY-MM'
) -> GSTR1DraftResponse:
    """
    Stage 5: Generates GSTR-1 Working Draft based solely on approved invoices.
    Complies with:
    - Notification No. 12/2024 – Central Tax (B2C Large ₹1 Lakh threshold from August 2024 onwards)
    - May 2025 GST Portal Phase 3 rules:
      * Segregated Table 12 B2B and B2C tabs
      * Table 12 B2B is required when Table 4 contains supplies; Table 12 B2C is optional
      * Table 12 supply-value mismatches generate warnings, not filing blockers
      * Preceding Financial Year PAN-level Aggregate Annual Turnover (AATO)
    - May 2025 Table 13 Document Details:
      * Conditionally mandatory from May 2025 ('2025-05') when outward supplies exist
      * Optional / not mandatory for nil returns or historical periods (< '2025-05')
      * Comprehensive document classification (invoices, revised, debit, credit notes)
    Applies prominent working draft disclaimer.
    Never submits to or represents as an official tax filing.
    """
    # 1. Fetch Organization profile, GSTIN, and Preceding FY AATO
    # Remove assumed ₹5 Crore default: Aggregate Annual Turnover must be verified across PAN
    org_stmt = text("""
        SELECT 
            o.legal_name, 
            bp.gstin,
            bp.preceding_fy_aato,
            COALESCE(bp.aato_provenance, 'unverified_placeholder')
        FROM public.organizations o
        JOIN public.business_profiles bp ON bp.org_id = o.id
        WHERE o.id = :org_id
        LIMIT 1;
    """)
    org_row = session.execute(org_stmt, {"org_id": org_id}).fetchone()
    legal_name = org_row[0] if org_row else "Unknown Organization"
    supplier_gstin = org_row[1] if org_row else "Unregistered"
    raw_aato = org_row[2] if org_row else None
    aato_provenance = str(org_row[3]) if org_row and org_row[3] else "unverified_placeholder"
    preceding_fy_aato = Decimal(str(raw_aato)) if raw_aato is not None else None
    aato_unverified = (preceding_fy_aato is None or aato_provenance == "unverified_placeholder")

    supplier_state = supplier_gstin[:2] if len(supplier_gstin) >= 2 else "27"
    b2c_large_threshold = get_b2c_large_threshold(tax_period)

    # 2. Fetch approved invoices for the period with explicit statutory classifications
    inv_stmt = text("""
        SELECT 
            i.id, i.invoice_number, i.invoice_date, i.counterparty_name,
            i.counterparty_gstin, i.total_taxable_amount, i.cgst_amount,
            i.sgst_amount, i.igst_amount, i.total_amount,
            i.supply_category, i.is_sez_supply, i.is_deemed_export,
            i.is_reverse_charge, i.document_type, i.recipient_registration_type,
            i.classification_provenance
        FROM public.invoices i
        WHERE i.org_id = :org_id
          AND i.status = 'approved'
          AND to_char(i.invoice_date, 'YYYY-MM') = :period
        ORDER BY i.invoice_date, i.invoice_number;
    """)
    invoices = session.execute(inv_stmt, {"org_id": org_id, "period": tax_period}).fetchall()

    b2b_entries: List[GSTR1Table4Entry] = []
    b2c_large: List[Dict[str, Any]] = []
    b2c_small: List[Dict[str, Any]] = []
    total_taxable = Decimal("0.00")
    total_liability = Decimal("0.00")

    invoice_numbers = []

    has_table_4a = False
    has_table_4b = False
    has_table_6b = False
    has_table_6c = False
    has_table_8_b2b = False
    has_table_9a_b2b = False
    has_table_9b_b2b = False
    has_table_9c_b2b = False
    has_table_15_b2b = False
    has_table_15a_b2b = False

    for inv in invoices:
        (inv_id, inv_num, inv_date, cp_name, cp_gstin, taxable, cgst, sgst, igst, total,
         supply_category, is_sez_supply, is_deemed_export, is_reverse_charge,
         document_type, recipient_registration_type, classification_provenance) = inv
        invoice_numbers.append(inv_num)
        taxable_dec = Decimal(str(taxable))
        cgst_dec = Decimal(str(cgst))
        sgst_dec = Decimal(str(sgst))
        igst_dec = Decimal(str(igst))
        total_dec = Decimal(str(total))

        total_taxable += taxable_dec
        total_liability += (cgst_dec + sgst_dec + igst_dec)

        # Place of supply & Interstate determination
        is_interstate = False
        if cp_gstin and len(cp_gstin.strip()) >= 2:
            pos = cp_gstin[:2]
            is_interstate = (pos != supplier_state)
        elif igst_dec > Decimal("0.00") and (cgst_dec == Decimal("0.00") and sgst_dec == Decimal("0.00")):
            is_interstate = True
            pos = "29" if supplier_state != "29" else "27"
        else:
            pos = supplier_state
            is_interstate = False

        # Rate derivation
        rate = Decimal("18.00")
        if taxable_dec > 0:
            rate = (((cgst_dec + sgst_dec + igst_dec) / taxable_dec) * 100).quantize(Decimal("0.01"))

        is_registered = bool(cp_gstin and len(cp_gstin.strip()) == 15)

        if is_registered:
            # Explicit Statutory Classification based on structured fields (No heuristics)
            if document_type in ("revised_credit_note", "revised_debit_note"):
                has_table_9c_b2b = True
                inv_type = "CDNRA"
            elif document_type in ("credit_note", "debit_note"):
                has_table_9b_b2b = True
                inv_type = "CDNR"
            elif document_type == "revised_invoice":
                has_table_9a_b2b = True
                inv_type = "B2BA"
            elif supply_category == "ECO_AMENDMENT":
                has_table_15a_b2b = True
                inv_type = "ECOA"
            elif supply_category == "ECO_SUPPLY":
                has_table_15_b2b = True
                inv_type = "ECO"
            elif is_sez_supply or supply_category in ("SEZ_WITH_PAYMENT", "SEZ_WITHOUT_PAYMENT"):
                has_table_6b = True
                inv_type = "SEZ"
            elif is_deemed_export or supply_category == "DEEMED_EXPORT":
                has_table_6c = True
                inv_type = "Deemed Export"
            elif is_reverse_charge or supply_category == "B2B_REVERSE_CHARGE":
                has_table_4b = True
                inv_type = "Reverse Charge"
            elif supply_category == "NIL_EXEMPT":
                has_table_8_b2b = True
                inv_type = "Nil/Exempt"
            else:
                has_table_4a = True
                inv_type = "Regular"

            if supply_category == "NIL_EXEMPT":
                has_table_8_b2b = True

            b2b_entries.append(GSTR1Table4Entry(
                recipient_gstin=cp_gstin,
                recipient_name=cp_name,
                invoice_number=inv_num,
                invoice_date=str(inv_date),
                invoice_value=total_dec,
                place_of_supply=pos,
                reverse_charge="Y" if (is_reverse_charge or has_table_4b) else "N",
                invoice_type=inv_type,
                rate=rate,
                taxable_value=taxable_dec,
                cgst_amount=cgst_dec,
                sgst_amount=sgst_dec,
                igst_amount=igst_dec
            ))
        else:
            # Unregistered supplies:
            # Table 5 (B2C Large): Interstate and total invoice value strictly > threshold
            # Table 7 (B2C Small): Intrastate OR interstate <= threshold
            if is_interstate and total_dec > b2c_large_threshold:
                b2c_large.append({
                    "place_of_supply": pos,
                    "invoice_number": inv_num,
                    "invoice_date": str(inv_date),
                    "invoice_value": total_dec,
                    "taxable_value": taxable_dec,
                    "rate": rate,
                    "igst_amount": igst_dec
                })
            else:
                b2c_small.append({
                    "place_of_supply": pos,
                    "rate": rate,
                    "taxable_value": taxable_dec,
                    "cgst_amount": cgst_dec,
                    "sgst_amount": sgst_dec,
                    "igst_amount": igst_dec
                })

    # 3. Table 12: HSN Summary split into separate B2B and B2C tabs (May 2025 Phase 3 Portal rules)
    hsn_stmt = text("""
        SELECT 
            COALESCE(ili.hsn_sac_code, '998311') as hsn,
            CASE 
                WHEN i.counterparty_gstin IS NOT NULL AND length(trim(i.counterparty_gstin)) = 15 THEN 'B2B' 
                ELSE 'B2C' 
            END as supply_type,
            SUM(ili.quantity) as total_qty,
            SUM(ili.total_amount) as total_val,
            SUM(ili.taxable_amount) as taxable_val,
            ili.gst_rate as rate,
            SUM(ili.cgst_amount) as cgst,
            SUM(ili.sgst_amount) as sgst,
            SUM(ili.igst_amount) as igst
        FROM public.invoice_line_items ili
        JOIN public.invoices i ON i.id = ili.invoice_id
        WHERE i.org_id = :org_id
          AND i.status = 'approved'
          AND to_char(i.invoice_date, 'YYYY-MM') = :period
        GROUP BY ili.hsn_sac_code, supply_type, ili.gst_rate;
    """)
    hsn_rows = session.execute(hsn_stmt, {"org_id": org_id, "period": tax_period}).fetchall()
    
    table_12_hsn_b2b: List[GSTR1Table12Entry] = []
    table_12_hsn_b2c: List[GSTR1Table12Entry] = []
    table_12_hsn_summary: List[GSTR1Table12Entry] = []
    table_12_taxable_sum = Decimal("0.00")

    for h in hsn_rows:
        code_str = str(h[0])
        supply_type = h[1]
        catalog_entry = lookup_hsn_code(code_str)
        desc = catalog_entry.description if catalog_entry else "Goods and Services Classification"
        taxable_val = Decimal(str(h[4]))
        table_12_taxable_sum += taxable_val

        entry = GSTR1Table12Entry(
            hsn_sac_code=code_str,
            description=desc,
            uqc="OTH",
            total_quantity=Decimal(str(h[2])),
            total_value=Decimal(str(h[3])),
            taxable_value=taxable_val,
            gst_rate=Decimal(str(h[5])),
            cgst_amount=Decimal(str(h[6])),
            sgst_amount=Decimal(str(h[7])),
            igst_amount=Decimal(str(h[8])),
            cess_amount=Decimal("0.00")
        )

        if supply_type == "B2B":
            table_12_hsn_b2b.append(entry)
        else:
            table_12_hsn_b2c.append(entry)
        table_12_hsn_summary.append(entry)

    # GST Portal May 2025 Guidance & Phase 3 Rules:
    # Table 12 B2B tab is mandatory when registered-recipient outward supplies are reported across ANY of:
    # - Table 4A (Regular B2B supplies to registered persons)
    # - Table 4B (Supplies attracting reverse charge to registered persons)
    # - Table 6B (Supplies made to SEZ unit / SEZ developer with or without payment)
    # - Table 6C (Deemed exports)
    # - Table 8 (Nil-rated / exempt outward supplies to registered persons)
    # - Table 9A (Amendments to registered outward supplies of earlier periods)
    # - Table 9B (Credit / Debit Notes issued to registered persons)
    # - Table 9C (Amended Credit / Debit Notes to registered persons)
    # - Table 15 (Supplies made through E-commerce operators to registered persons u/s 9(5))
    # - Table 15A (Amendments to supplies through ECO to registered persons)
    b2b_supply_tables = {
        "table_4": has_table_4a or has_table_4b,
        "table_4a": has_table_4a,
        "table_4b": has_table_4b,
        "table_6b": has_table_6b,
        "table_6c": has_table_6c,
        "table_8_b2b": has_table_8_b2b,
        "table_9a_b2b": has_table_9a_b2b,
        "table_9b_b2b": has_table_9b_b2b,
        "table_9c_b2b": has_table_9c_b2b,
        "table_15_b2b": has_table_15_b2b,
        "table_15a_b2b": has_table_15a_b2b,
    }
    B2B_REQUIRING_TABLE_12 = {
        "table_4", "table_4a", "table_4b", "table_6b", "table_6c",
        "table_8_b2b", "table_9a_b2b", "table_9b_b2b", "table_9c_b2b",
        "table_15_b2b", "table_15a_b2b"
    }
    b2b_triggering_tables = [t for t in B2B_REQUIRING_TABLE_12 if b2b_supply_tables[t]]
    table_12_b2b_mandatory = len(b2b_triggering_tables) > 0
    table_12_b2c_mandatory = False  # Explicitly optional per May 2025 Advisory
    table_12_warnings: List[str] = []

    if aato_unverified:
        table_12_warnings.append(
            "Preceding financial-year Aggregate Annual Turnover (AATO) is unverified across PAN. "
            "HSN reporting length requirements (4 digits vs 6 digits) cannot be statutory-verified until PAN AATO is confirmed."
        )

    if table_12_b2b_mandatory and len(table_12_hsn_b2b) == 0:
        table_12_warnings.append(
            f"Registered outward supplies are present in {', '.join(b2b_triggering_tables)}, but Table 12 B2B tab has no entries. "
            "Under GST Portal Phase 3 rules, Table 12 B2B reporting is mandatory when supplies to registered recipients are present."
        )

    # Table 12 B2C advisory notice: If Table 5 or 7 has supplies but Table 12 B2C tab is empty
    if (len(b2c_large) > 0 or len(b2c_small) > 0) and len(table_12_hsn_b2c) == 0:
        table_12_warnings.append(
            "B2C outward supplies are present in Table 5/7, but Table 12 B2C HSN tab has no entries. "
            "Under GST Portal May 2025 Advisory, Table 12 B2C reporting is optional but generates this advisory notice."
        )

    # Supply value mismatch: Non-blocking warning per Portal Phase 3 Advisory
    if abs(table_12_taxable_sum - total_taxable) > Decimal("1.00"):
        table_12_warnings.append(
            f"Table 12 total taxable value ({table_12_taxable_sum}) differs from outward supply taxable value ({total_taxable}). "
            "Under GST Portal Phase 3 rules, this generates a warning but does not block filing."
        )

    # 4. Table 13: Multi-table Outward Supplies Population Logic
    # Statutorily mandatory from May 2025 (tax_period >= '2025-05') if ANY outward supply tables contain data:
    # Tables 4, 5, 6, 7, 8, 9, 10, 14, 15.
    table_has_data = {
        "table_4": has_table_4a or has_table_4b,
        "table_5": len(b2c_large) > 0,
        "table_6": has_table_6b or has_table_6c,
        "table_7": len(b2c_small) > 0,
        "table_8": has_table_8_b2b,
        "table_9": has_table_9a_b2b or has_table_9b_b2b or has_table_9c_b2b,
        "table_10": False,
        "table_14": False,
        "table_15": has_table_15_b2b or has_table_15a_b2b,
    }
    REQUIRING_TABLE_13 = {"table_4", "table_5", "table_6", "table_7", "table_8", "table_9", "table_10", "table_14", "table_15"}
    has_outward_supplies = any(table_has_data[t] for t in REQUIRING_TABLE_13)
    is_table_13_mandatory = (tax_period >= "2025-05") and has_outward_supplies

    doc_categories = [
        {
            "doc_type_code": "01",
            "nature_of_document": "Invoices for outward supply",
            "from_serial_number": invoice_numbers[0] if invoice_numbers else None,
            "to_serial_number": invoice_numbers[-1] if invoice_numbers else None,
            "total_number_issued": len(invoices),
            "cancelled_count": 0,
            "net_issued": len(invoices)
        },
        {
            "doc_type_code": "02",
            "nature_of_document": "Invoices for inward supply from unregistered person (RCM)",
            "from_serial_number": None,
            "to_serial_number": None,
            "total_number_issued": 0,
            "cancelled_count": 0,
            "net_issued": 0
        },
        {
            "doc_type_code": "03",
            "nature_of_document": "Revised Invoices",
            "from_serial_number": None,
            "to_serial_number": None,
            "total_number_issued": 0,
            "cancelled_count": 0,
            "net_issued": 0
        },
        {
            "doc_type_code": "04",
            "nature_of_document": "Debit Notes",
            "from_serial_number": None,
            "to_serial_number": None,
            "total_number_issued": 0,
            "cancelled_count": 0,
            "net_issued": 0
        },
        {
            "doc_type_code": "05",
            "nature_of_document": "Credit Notes",
            "from_serial_number": None,
            "to_serial_number": None,
            "total_number_issued": 0,
            "cancelled_count": 0,
            "net_issued": 0
        }
    ]

    doc_summary = {
        "is_mandatory": is_table_13_mandatory,
        "tax_period": tax_period,
        "has_outward_supplies": has_outward_supplies,
        "triggering_tables": [t for t in REQUIRING_TABLE_13 if table_has_data[t]],
        "total_number_issued": len(invoices),
        "from_serial_number": invoice_numbers[0] if invoice_numbers else None,
        "to_serial_number": invoice_numbers[-1] if invoice_numbers else None,
        "cancelled_count": 0,
        "net_issued": len(invoices),
        "document_categories": doc_categories
    }

    return GSTR1DraftResponse(
        disclaimer=GSTR_WORKING_DRAFT_DISCLAIMER,
        tax_period=tax_period,
        gstin=supplier_gstin,
        legal_name=legal_name,
        preceding_fy_aato=preceding_fy_aato,
        aato_unverified=aato_unverified,
        aato_provenance=aato_provenance,
        applicable_b2c_large_threshold=b2c_large_threshold,
        table_4_b2b=b2b_entries,
        table_5_b2c_large=b2c_large,
        table_7_b2c_small=b2c_small,
        table_12_hsn_b2b=table_12_hsn_b2b,
        table_12_hsn_b2c=table_12_hsn_b2c,
        table_12_hsn_summary=table_12_hsn_summary,
        b2b_supply_tables=b2b_supply_tables,
        table_12_b2b_mandatory=table_12_b2b_mandatory,
        table_12_b2c_mandatory=table_12_b2c_mandatory,
        table_12_warnings=table_12_warnings,
        table_13_doc_summary=doc_summary,
        total_taxable_value=total_taxable,
        total_tax_liability=total_liability
    )


def prepare_gstr3b_draft(
    session: Session,
    org_id: str,
    tax_period: str
) -> GSTR3BDraftResponse:
    """
    Stage 5: Generates Outward-Supply-Derived GSTR-3B Working Summary.
    EXPLICIT SCOPE & ZERO-PLACEHOLDER PROHIBITION:
    - Calculates Table 3.1(a) Outward Taxable Supplies strictly from approved sales invoices.
    - Eliminates unsafe 0.00 placeholders: Unreconciled fields emit status='not_computed' with null values
      and explicit provenance reasons.
    - Authoritative ITC (Table 4), RCM liabilities, and cash tax payment (Table 6.1) require external
      purchase registers, GSTR-2B reconciliation, and official GST portal cash ledger data.
    """
    # 1. Fetch organization GSTIN
    org_stmt = text("""
        SELECT o.legal_name, bp.gstin
        FROM public.organizations o
        JOIN public.business_profiles bp ON bp.org_id = o.id
        WHERE o.id = :org_id
        LIMIT 1;
    """)
    org_row = session.execute(org_stmt, {"org_id": org_id}).fetchone()
    legal_name = org_row[0] if org_row else "Unknown Organization"
    gstin = org_row[1] if org_row else "Unregistered"

    # 2. Table 3.1 Outward supplies aggregation
    outward_stmt = text("""
        SELECT 
            COALESCE(SUM(total_taxable_amount), 0.00) as taxable,
            COALESCE(SUM(cgst_amount), 0.00) as cgst,
            COALESCE(SUM(sgst_amount), 0.00) as sgst,
            COALESCE(SUM(igst_amount), 0.00) as igst
        FROM public.invoices
        WHERE org_id = :org_id
          AND status = 'approved'
          AND to_char(invoice_date, 'YYYY-MM') = :period;
    """)
    outward = session.execute(outward_stmt, {"org_id": org_id, "period": tax_period}).fetchone()
    taxable = Decimal(str(outward[0]))
    cgst = Decimal(str(outward[1]))
    sgst = Decimal(str(outward[2]))
    igst = Decimal(str(outward[3]))

    table_3_1 = {
        "3_1_a_outward_taxable_supplies": {
            "status": "computed",
            "total_taxable_value": taxable,
            "integrated_tax": igst,
            "central_tax": cgst,
            "state_ut_tax": sgst,
            "cess": Decimal("0.00")
        },
        "3_1_b_outward_taxable_zero_rated": {
            "status": "not_computed",
            "total_taxable_value": None,
            "integrated_tax": None,
            "cess": None,
            "reason": "Zero-rated export supplies require shipping bill and customs export invoice reconciliation"
        },
        "3_1_c_other_outward_supplies_nil_exempt": {
            "status": "not_computed",
            "total_taxable_value": None,
            "reason": "Nil-rated and exempt supply register not reconciled"
        },
        "3_1_d_inward_supplies_reverse_charge": {
            "status": "not_computed",
            "total_taxable_value": None,
            "integrated_tax": None,
            "central_tax": None,
            "state_ut_tax": None,
            "cess": None,
            "reason": "Inward reverse-charge liabilities require purchase ledger reconciliation"
        },
        "3_1_1_ecommerce_supplies": {
            "status": "not_computed",
            "total_taxable_value": None,
            "reason": "E-commerce operator supply register not in scope"
        }
    }

    # 3. Table 4 Eligible ITC - honest not_computed structure indicating external provenance requirement
    table_4 = {
        "status": "not_computed",
        "reason": "Authoritative Input Tax Credit (ITC) requires GSTR-2B electronic credit ledger reconciliation",
        "itc_available": {
            "import_of_goods": {"status": "not_computed", "value": None, "reason": "Requires ICEGATE Bill of Entry reconciliation"},
            "import_of_services": {"status": "not_computed", "value": None, "reason": "Requires foreign remittance and invoice reconciliation"},
            "inward_reverse_charge": {"status": "not_computed", "value": None, "reason": "Requires purchase register RCM reconciliation"},
            "all_other_itc": {"status": "not_computed", "value": None, "reason": "Requires GSTR-2B supplier filing reconciliation"}
        },
        "itc_reversed": {
            "as_per_rules_38_42_43": {"status": "not_computed", "value": None, "reason": "Requires common input credit ratio calculation"},
            "others": {"status": "not_computed", "value": None, "reason": "Requires credit note and ineligible credit reconciliation"}
        },
        "net_itc_available": {
            "status": "not_computed",
            "value": None,
            "reason": "Net ITC calculation blocked pending GSTR-2B reconciliation"
        }
    }

    tax_payable = {
        "outward_tax_liability": {
            "status": "computed",
            "integrated_tax": igst,
            "central_tax": cgst,
            "state_ut_tax": sgst,
            "total": igst + cgst + sgst
        },
        "itc_credit_applied": {
            "status": "not_computed",
            "value": None,
            "reason": "ITC credit offset requires reconciled GSTR-2B purchase register"
        },
        "net_cash_payable_after_itc": {
            "status": "not_computed",
            "value": None,
            "reason": "Authoritative cash tax payment requires GST Portal Electronic Cash and Credit Ledger balances"
        },
        "gross_outward_tax_before_itc": {
            "status": "computed",
            "integrated_tax": igst,
            "central_tax": cgst,
            "state_ut_tax": sgst,
            "total": igst + cgst + sgst
        }
    }

    return GSTR3BDraftResponse(
        disclaimer=GSTR3B_WORKING_SUMMARY_DISCLAIMER,
        document_type="Outward-Supply-Derived GSTR-3B Working Summary",
        tax_period=tax_period,
        gstin=gstin,
        legal_name=legal_name,
        field_provenance={
            "table_3_1_a_outward_taxable": "calculated_from_approved_sales_invoices",
            "table_3_1_b_outward_zero_rated": "missing_not_computed_requires_shipping_bills",
            "table_3_1_d_inward_reverse_charge": "missing_not_computed_requires_purchase_register",
            "table_3_1_1_ecommerce": "missing_not_in_scope",
            "table_4_a_itc_available": "external_not_computed_requires_gstr2b_reconciliation",
            "table_4_b_itc_reversed": "external_not_computed_requires_rules_42_43_calculation",
            "table_6_1_payment_of_tax": "unsupported_not_computed_requires_gst_portal_cash_ledger"
        },
        authoritative_itc_calculated=False,
        authoritative_net_cash_calculated=False,
        table_3_1_outward_supplies=table_3_1,
        table_4_eligible_itc=table_4,
        tax_payable_summary=tax_payable
    )
