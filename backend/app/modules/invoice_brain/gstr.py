from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
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


def _resolve_profile_row(session: Session, org_id: str, profile_id: Optional[str]):
    if profile_id:
        org_stmt = text("""
            SELECT
                o.legal_name,
                bp.gstin,
                bp.preceding_fy_aato,
                COALESCE(bp.aato_provenance, 'unverified_placeholder')
            FROM public.organizations o
            JOIN public.business_profiles bp ON bp.org_id = o.id
            WHERE o.id = :org_id AND bp.id = :profile_id
            LIMIT 1;
        """)
        return session.execute(org_stmt, {"org_id": org_id, "profile_id": profile_id}).fetchone()
    org_stmt = text("""
        SELECT
            o.legal_name,
            bp.gstin,
            bp.preceding_fy_aato,
            COALESCE(bp.aato_provenance, 'unverified_placeholder')
        FROM public.organizations o
        JOIN public.business_profiles bp ON bp.org_id = o.id
        WHERE o.id = :org_id
        ORDER BY bp.updated_at DESC, bp.created_at DESC
        LIMIT 1;
    """)
    return session.execute(org_stmt, {"org_id": org_id}).fetchone()


def prepare_gstr1_draft(
    session: Session,
    org_id: str,
    tax_period: str,  # Format: 'YYYY-MM'
    profile_id: Optional[str] = None,
) -> GSTR1DraftResponse:
    """
    Stage 5: Generates GSTR-1 Working Draft based solely on approved invoices.
    Scopes strictly by profile_id when specified (preventing multi-GSTIN contamination),
    aggregates B2C Small properly by (place_of_supply, rate), preserves real HSN/SAC
    values without fabrication, handles Credit Note signs cleanly, and populates Table 13 counts.
    """
    org_row = _resolve_profile_row(session, org_id, profile_id)
    legal_name = org_row[0] if org_row else "Unknown Organization"
    supplier_gstin = org_row[1] if org_row else "Unregistered"
    raw_aato = org_row[2] if org_row else None
    aato_provenance = str(org_row[3]) if org_row and org_row[3] else "unverified_placeholder"
    preceding_fy_aato = Decimal(str(raw_aato)) if raw_aato is not None else None
    aato_unverified = (preceding_fy_aato is None or aato_provenance == "unverified_placeholder")

    supplier_state = supplier_gstin[:2] if len(supplier_gstin) >= 2 else "27"
    b2c_large_threshold = get_b2c_large_threshold(tax_period)

    profile_clause = " AND i.profile_id = :profile_id" if profile_id else ""
    inv_params: Dict[str, Any] = {"org_id": org_id, "period": tax_period}
    if profile_id:
        inv_params["profile_id"] = profile_id

    inv_stmt = text(f"""
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
          {profile_clause}
        ORDER BY i.invoice_date, i.invoice_number;
    """)
    invoices = session.execute(inv_stmt, inv_params).fetchall()

    b2b_entries: List[GSTR1Table4Entry] = []
    b2c_large: List[Dict[str, Any]] = []
    b2c_small_map: Dict[Tuple[str, Decimal], Dict[str, Any]] = {}
    total_taxable = Decimal("0.00")
    total_liability = Decimal("0.00")

    doc_nums_by_code: Dict[str, List[str]] = {"01": [], "02": [], "03": [], "04": [], "05": []}

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

        taxable_dec = Decimal(str(taxable if taxable is not None else 0))
        cgst_dec = Decimal(str(cgst if cgst is not None else 0))
        sgst_dec = Decimal(str(sgst if sgst is not None else 0))
        igst_dec = Decimal(str(igst if igst is not None else 0))
        total_dec = Decimal(str(total if total is not None else 0))

        is_credit_note = document_type in ("credit_note", "revised_credit_note")
        sign = Decimal("-1") if is_credit_note else Decimal("1")

        # Document categorization for Table 13
        if document_type in ("credit_note", "revised_credit_note"):
            doc_nums_by_code["05"].append(inv_num)
        elif document_type in ("debit_note", "revised_debit_note"):
            doc_nums_by_code["04"].append(inv_num)
        elif document_type == "revised_invoice":
            doc_nums_by_code["03"].append(inv_num)
        else:
            doc_nums_by_code["01"].append(inv_num)

        total_taxable += sign * taxable_dec
        total_liability += sign * (cgst_dec + sgst_dec + igst_dec)

        is_interstate = False
        if cp_gstin and len(cp_gstin.strip()) >= 2:
            pos = cp_gstin[:2]
            is_interstate = (pos != supplier_state)
        elif igst_dec > Decimal("0.00") and (cgst_dec == Decimal("0.00") and sgst_dec == Decimal("0.00")):
            is_interstate = True
            pos = "29" if supplier_state != "29" else "27"
        else:
            pos = supplier_state

        rate = Decimal("0.00") if supply_category == "NIL_EXEMPT" else Decimal("18.00")
        if taxable_dec > 0:
            rate = (((cgst_dec + sgst_dec + igst_dec) / taxable_dec) * 100).quantize(Decimal("0.01"))

        is_registered = bool(cp_gstin and len(cp_gstin.strip()) == 15)

        if is_registered:
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
                igst_amount=igst_dec,
            ))
        else:
            if is_interstate and total_dec > b2c_large_threshold:
                b2c_large.append({
                    "place_of_supply": pos,
                    "invoice_number": inv_num,
                    "invoice_date": str(inv_date),
                    "invoice_value": total_dec,
                    "taxable_value": taxable_dec,
                    "rate": rate,
                    "igst_amount": igst_dec,
                })
            else:
                b2c_key = (pos, rate)
                bucket = b2c_small_map.setdefault(
                    b2c_key,
                    {
                        "place_of_supply": pos,
                        "rate": rate,
                        "taxable_value": Decimal("0.00"),
                        "cgst_amount": Decimal("0.00"),
                        "sgst_amount": Decimal("0.00"),
                        "igst_amount": Decimal("0.00"),
                    },
                )
                bucket["taxable_value"] += sign * taxable_dec
                bucket["cgst_amount"] += sign * cgst_dec
                bucket["sgst_amount"] += sign * sgst_dec
                bucket["igst_amount"] += sign * igst_dec

    b2c_small = list(b2c_small_map.values())

    # 3. Table 12: HSN Summary without fabricating '998311' or crashing on NULL line columns
    hsn_stmt = text(f"""
        SELECT
            COALESCE(NULLIF(btrim(ili.hsn_sac_code), ''), 'UNCLASSIFIED') as hsn,
            CASE
                WHEN i.counterparty_gstin IS NOT NULL AND length(trim(i.counterparty_gstin)) = 15 THEN 'B2B'
                ELSE 'B2C'
            END as supply_type,
            COALESCE(SUM(COALESCE(ili.quantity, 0)), 0) as total_qty,
            COALESCE(SUM(COALESCE(ili.total_amount, 0)), 0) as total_val,
            COALESCE(SUM(COALESCE(ili.taxable_amount, 0)), 0) as taxable_val,
            COALESCE(ili.gst_rate, 0) as rate,
            COALESCE(SUM(COALESCE(ili.cgst_amount, 0)), 0) as cgst,
            COALESCE(SUM(COALESCE(ili.sgst_amount, 0)), 0) as sgst,
            COALESCE(SUM(COALESCE(ili.igst_amount, 0)), 0) as igst
        FROM public.invoice_line_items ili
        JOIN public.invoices i ON i.id = ili.invoice_id
        WHERE i.org_id = :org_id
          AND i.status = 'approved'
          AND to_char(i.invoice_date, 'YYYY-MM') = :period
          {profile_clause}
        GROUP BY 1, 2, COALESCE(ili.gst_rate, 0);
    """)
    hsn_rows = session.execute(hsn_stmt, inv_params).fetchall()

    table_12_hsn_b2b: List[GSTR1Table12Entry] = []
    table_12_hsn_b2c: List[GSTR1Table12Entry] = []
    table_12_hsn_summary: List[GSTR1Table12Entry] = []
    table_12_taxable_sum = Decimal("0.00")

    for h in hsn_rows:
        code_str = str(h[0])
        supply_type = h[1]
        catalog_entry = lookup_hsn_code(code_str) if code_str != "UNCLASSIFIED" else None
        desc = catalog_entry.description if catalog_entry else (
            "Unclassified Item (Missing HSN/SAC)" if code_str == "UNCLASSIFIED" else "Goods and Services Classification"
        )
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
            cess_amount=Decimal("0.00"),
        )

        if supply_type == "B2B":
            table_12_hsn_b2b.append(entry)
        else:
            table_12_hsn_b2c.append(entry)
        table_12_hsn_summary.append(entry)

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
        "table_15_b2b", "table_15a_b2b",
    }
    b2b_triggering_tables = [t for t in B2B_REQUIRING_TABLE_12 if b2b_supply_tables[t]]
    table_12_b2b_mandatory = len(b2b_triggering_tables) > 0
    table_12_b2c_mandatory = False
    table_12_warnings: List[str] = []

    if aato_unverified:
        table_12_warnings.append(
            "Preceding financial-year Aggregate Annual Turnover (AATO) is unverified across PAN. "
            "HSN reporting length requirements (4 digits vs 6 digits) cannot be statutory-verified until PAN AATO is confirmed."
        )

    if any(e.hsn_sac_code == "UNCLASSIFIED" for e in table_12_hsn_summary):
        table_12_warnings.append(
            "One or more line items lack an HSN/SAC code ('UNCLASSIFIED'). Provide valid HSN/SAC before filing Table 12."
        )

    if table_12_b2b_mandatory and len(table_12_hsn_b2b) == 0:
        table_12_warnings.append(
            f"Registered outward supplies are present in {', '.join(b2b_triggering_tables)}, but Table 12 B2B tab has no entries. "
            "Under GST Portal Phase 3 rules, Table 12 B2B reporting is mandatory when supplies to registered recipients are present."
        )

    if (len(b2c_large) > 0 or len(b2c_small) > 0) and len(table_12_hsn_b2c) == 0:
        table_12_warnings.append(
            "B2C outward supplies are present in Table 5/7, but Table 12 B2C HSN tab has no entries. "
            "Under GST Portal May 2025 Advisory, Table 12 B2C reporting is optional but generates this advisory notice."
        )

    if abs(table_12_taxable_sum - total_taxable) > Decimal("1.00"):
        table_12_warnings.append(
            f"Table 12 total taxable value ({table_12_taxable_sum}) differs from outward supply taxable value ({total_taxable}). "
            "Under GST Portal Phase 3 rules, this generates a warning but does not block filing."
        )

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

    def _cat_entry(code: str, nature: str):
        nums = doc_nums_by_code[code]
        return {
            "doc_type_code": code,
            "nature_of_document": nature,
            "from_serial_number": nums[0] if nums else None,
            "to_serial_number": nums[-1] if nums else None,
            "total_number_issued": len(nums),
            "cancelled_count": 0,
            "net_issued": len(nums),
        }

    doc_categories = [
        _cat_entry("01", "Invoices for outward supply"),
        _cat_entry("02", "Invoices for inward supply from unregistered person (RCM)"),
        _cat_entry("03", "Revised Invoices"),
        _cat_entry("04", "Debit Notes"),
        _cat_entry("05", "Credit Notes"),
    ]

    all_nums = doc_nums_by_code["01"] or [inv[1] for inv in invoices]
    doc_summary = {
        "is_mandatory": is_table_13_mandatory,
        "tax_period": tax_period,
        "has_outward_supplies": has_outward_supplies,
        "triggering_tables": [t for t in REQUIRING_TABLE_13 if table_has_data[t]],
        "total_number_issued": len(invoices),
        "from_serial_number": all_nums[0] if all_nums else None,
        "to_serial_number": all_nums[-1] if all_nums else None,
        "cancelled_count": 0,
        "net_issued": len(invoices),
        "document_categories": doc_categories,
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
        total_tax_liability=total_tax_liability if (total_tax_liability := total_liability) is not None else Decimal("0.00"),
    )


def prepare_gstr3b_draft(
    session: Session,
    org_id: str,
    tax_period: str,
    profile_id: Optional[str] = None,
) -> GSTR3BDraftResponse:
    """
    Stage 5: Generates Outward-Supply-Derived GSTR-3B Working Summary, scoped by profile_id
    when provided and segregated by supply_category / credit_note sign.
    """
    org_row = _resolve_profile_row(session, org_id, profile_id)
    legal_name = org_row[0] if org_row else "Unknown Organization"
    gstin = org_row[1] if org_row else "Unregistered"

    profile_clause = " AND profile_id = :profile_id" if profile_id else ""
    params: Dict[str, Any] = {"org_id": org_id, "period": tax_period}
    if profile_id:
        params["profile_id"] = profile_id

    outward_stmt = text(f"""
        SELECT
            supply_category, is_sez_supply, is_reverse_charge, document_type,
            total_taxable_amount, cgst_amount, sgst_amount, igst_amount
        FROM public.invoices
        WHERE org_id = :org_id
          AND status = 'approved'
          AND to_char(invoice_date, 'YYYY-MM') = :period
          {profile_clause};
    """)
    rows = session.execute(outward_stmt, params).fetchall()

    a_taxable = a_cgst = a_sgst = a_igst = Decimal("0.00")
    b_taxable = b_igst = Decimal("0.00")
    c_nil_exempt = Decimal("0.00")

    for sup_cat, is_sez, is_rcm, doc_type, t_val, c_val, s_val, i_val in rows:
        sign = Decimal("-1") if doc_type in ("credit_note", "revised_credit_note") else Decimal("1")
        tx = sign * Decimal(str(t_val or 0))
        cg = sign * Decimal(str(c_val or 0))
        sg = sign * Decimal(str(s_val or 0))
        ig = sign * Decimal(str(i_val or 0))
        if sup_cat == "NIL_EXEMPT":
            c_nil_exempt += tx
        elif is_sez or sup_cat in ("SEZ_WITH_PAYMENT", "SEZ_WITHOUT_PAYMENT"):
            b_taxable += tx
            b_igst += ig
        elif not is_rcm and sup_cat != "B2B_REVERSE_CHARGE":
            a_taxable += tx
            a_cgst += cg
            a_sgst += sg
            a_igst += ig

    table_3_1 = {
        "3_1_a_outward_taxable_supplies": {
            "status": "computed",
            "total_taxable_value": a_taxable,
            "integrated_tax": a_igst,
            "central_tax": a_cgst,
            "state_ut_tax": a_sgst,
            "cess": Decimal("0.00"),
        },
        "3_1_b_outward_taxable_zero_rated": {
            "status": "computed" if b_taxable > 0 else "not_computed",
            "total_taxable_value": b_taxable if b_taxable > 0 else None,
            "integrated_tax": b_igst if b_taxable > 0 else None,
            "cess": Decimal("0.00") if b_taxable > 0 else None,
            "reason": None if b_taxable > 0 else "Zero-rated export supplies require shipping bill and customs export invoice reconciliation",
        },
        "3_1_c_other_outward_supplies_nil_exempt": {
            "status": "computed" if c_nil_exempt > 0 else "not_computed",
            "total_taxable_value": c_nil_exempt if c_nil_exempt > 0 else None,
            "reason": None if c_nil_exempt > 0 else "Nil-rated and exempt supply register not reconciled",
        },
        "3_1_d_inward_supplies_reverse_charge": {
            "status": "not_computed",
            "total_taxable_value": None,
            "integrated_tax": None,
            "central_tax": None,
            "state_ut_tax": None,
            "cess": None,
            "reason": "Inward reverse-charge liabilities require purchase ledger reconciliation",
        },
        "3_1_1_ecommerce_supplies": {
            "status": "not_computed",
            "total_taxable_value": None,
            "reason": "E-commerce operator supply register not in scope",
        },
    }

    table_4 = {
        "status": "not_computed",
        "reason": "Authoritative Input Tax Credit (ITC) requires GSTR-2B electronic credit ledger reconciliation",
        "itc_available": {
            "import_of_goods": {"status": "not_computed", "value": None, "reason": "Requires ICEGATE Bill of Entry reconciliation"},
            "import_of_services": {"status": "not_computed", "value": None, "reason": "Requires foreign remittance and invoice reconciliation"},
            "inward_reverse_charge": {"status": "not_computed", "value": None, "reason": "Requires purchase register RCM reconciliation"},
            "all_other_itc": {"status": "not_computed", "value": None, "reason": "Requires GSTR-2B supplier filing reconciliation"},
        },
        "itc_reversed": {
            "as_per_rules_38_42_43": {"status": "not_computed", "value": None, "reason": "Requires common input credit ratio calculation"},
            "others": {"status": "not_computed", "value": None, "reason": "Requires credit note and ineligible credit reconciliation"},
        },
        "net_itc_available": {
            "status": "not_computed",
            "value": None,
            "reason": "Net ITC calculation blocked pending GSTR-2B reconciliation",
        },
    }

    gross_igst = a_igst + b_igst
    tax_payable = {
        "outward_tax_liability": {
            "status": "computed",
            "integrated_tax": gross_igst,
            "central_tax": a_cgst,
            "state_ut_tax": a_sgst,
            "total": gross_igst + a_cgst + a_sgst,
        },
        "itc_credit_applied": {
            "status": "not_computed",
            "value": None,
            "reason": "ITC credit offset requires reconciled GSTR-2B purchase register",
        },
        "net_cash_payable_after_itc": {
            "status": "not_computed",
            "value": None,
            "reason": "Authoritative cash tax payment requires GST Portal Electronic Cash and Credit Ledger balances",
        },
        "gross_outward_tax_before_itc": {
            "status": "computed",
            "integrated_tax": gross_igst,
            "central_tax": a_cgst,
            "state_ut_tax": a_sgst,
            "total": gross_igst + a_cgst + a_sgst,
        },
    }

    return GSTR3BDraftResponse(
        disclaimer=GSTR3B_WORKING_SUMMARY_DISCLAIMER,
        document_type="Outward-Supply-Derived GSTR-3B Working Summary",
        tax_period=tax_period,
        gstin=gstin,
        legal_name=legal_name,
        field_provenance={
            "table_3_1_a_outward_taxable": "calculated_from_approved_sales_invoices",
            "table_3_1_b_outward_zero_rated": "calculated_from_approved_sales_invoices" if b_taxable > 0 else "missing_not_computed_requires_shipping_bills",
            "table_3_1_d_inward_reverse_charge": "missing_not_computed_requires_purchase_register",
            "table_3_1_1_ecommerce": "missing_not_in_scope",
            "table_4_a_itc_available": "external_not_computed_requires_gstr2b_reconciliation",
            "table_4_b_itc_reversed": "external_not_computed_requires_rules_42_43_calculation",
            "table_6_1_payment_of_tax": "unsupported_not_computed_requires_gst_portal_cash_ledger",
        },
        authoritative_itc_calculated=False,
        authoritative_net_cash_calculated=False,
        table_3_1_outward_supplies=table_3_1,
        table_4_eligible_itc=table_4,
        tax_payable_summary=tax_payable,
    )
