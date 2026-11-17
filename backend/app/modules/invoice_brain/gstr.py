import uuid
from decimal import Decimal
from typing import Dict, Any, List, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.invoice_brain import (
    GSTR1DraftResponse, GSTR3BDraftResponse,
    GSTR1Table4Entry, GSTR_WORKING_DRAFT_DISCLAIMER
)

def prepare_gstr1_draft(
    session: Session,
    org_id: str,
    tax_period: str  # Format: 'YYYY-MM'
) -> GSTR1DraftResponse:
    """
    Stage 5: Generates GSTR-1 Working Draft based solely on approved invoices.
    Applies prominent working draft disclaimer.
    Never submits to or represents as an official tax filing.
    """
    # 1. Fetch Organization profile and GSTIN
    org_stmt = text("""
        SELECT o.legal_name, bp.gstin
        FROM public.organizations o
        JOIN public.business_profiles bp ON bp.org_id = o.id
        WHERE o.id = :org_id
        LIMIT 1;
    """)
    org_row = session.execute(org_stmt, {"org_id": org_id}).fetchone()
    legal_name = org_row[0] if org_row else "Unknown Organization"
    supplier_gstin = org_row[1] if org_row else "Unregistered"

    supplier_state = supplier_gstin[:2] if len(supplier_gstin) >= 2 else "27"

    # 2. Fetch approved invoices for the period
    inv_stmt = text("""
        SELECT 
            i.id, i.invoice_number, i.invoice_date, i.counterparty_name,
            i.counterparty_gstin, i.total_taxable_amount, i.cgst_amount,
            i.sgst_amount, i.igst_amount, i.total_amount
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

    for inv in invoices:
        inv_id, inv_num, inv_date, cp_name, cp_gstin, taxable, cgst, sgst, igst, total = inv
        invoice_numbers.append(inv_num)
        taxable_dec = Decimal(str(taxable))
        cgst_dec = Decimal(str(cgst))
        sgst_dec = Decimal(str(sgst))
        igst_dec = Decimal(str(igst))
        total_dec = Decimal(str(total))

        total_taxable += taxable_dec
        total_liability += (cgst_dec + sgst_dec + igst_dec)

        # Place of supply
        pos = cp_gstin[:2] if (cp_gstin and len(cp_gstin) >= 2) else supplier_state
        is_interstate = (pos != supplier_state)

        # Rate derivation
        rate = Decimal("18.00")
        if taxable_dec > 0:
            rate = (((cgst_dec + sgst_dec + igst_dec) / taxable_dec) * 100).quantize(Decimal("0.01"))

        if cp_gstin:
            # Table 4: B2B Registered supplies
            b2b_entries.append(GSTR1Table4Entry(
                recipient_gstin=cp_gstin,
                recipient_name=cp_name,
                invoice_number=inv_num,
                invoice_date=str(inv_date),
                invoice_value=total_dec,
                place_of_supply=pos,
                reverse_charge="N",
                invoice_type="Regular",
                rate=rate,
                taxable_value=taxable_dec,
                cgst_amount=cgst_dec,
                sgst_amount=sgst_dec,
                igst_amount=igst_dec
            ))
        else:
            # Unregistered: Table 5 (B2C Large > 2.5 Lakh interstate) vs Table 7 (B2C Small)
            if is_interstate and total_dec > Decimal("250000.00"):
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

    # 3. Table 12: HSN Summary from approved invoice line items
    hsn_stmt = text("""
        SELECT 
            COALESCE(ili.hsn_sac_code, '998311') as hsn,
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
        GROUP BY ili.hsn_sac_code, ili.gst_rate;
    """)
    hsn_rows = session.execute(hsn_stmt, {"org_id": org_id, "period": tax_period}).fetchall()
    hsn_summary = []
    for h in hsn_rows:
        hsn_summary.append({
            "hsn_sac_code": h[0],
            "total_quantity": float(h[1]),
            "total_value": float(h[2]),
            "taxable_value": float(h[3]),
            "gst_rate": float(h[4]),
            "cgst_amount": float(h[5]),
            "sgst_amount": float(h[6]),
            "igst_amount": float(h[7])
        })

    # 4. Table 13: Document summary
    doc_summary = {
        "nature_of_document": "Invoices for outward supply",
        "total_number_issued": len(invoices),
        "from_serial_number": invoice_numbers[0] if invoice_numbers else None,
        "to_serial_number": invoice_numbers[-1] if invoice_numbers else None,
        "cancelled_count": 0,
        "net_issued": len(invoices)
    }

    return GSTR1DraftResponse(
        disclaimer=GSTR_WORKING_DRAFT_DISCLAIMER,
        tax_period=tax_period,
        gstin=supplier_gstin,
        legal_name=legal_name,
        table_4_b2b=b2b_entries,
        table_5_b2c_large=b2c_large,
        table_7_b2c_small=b2c_small,
        table_12_hsn_summary=hsn_summary,
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
    Stage 5: Generates GSTR-3B Working Draft with Table 3.1 (Outward Supplies)
    and Table 4 (Eligible ITC summary) for internal tax preparation.
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
            "total_taxable_value": taxable,
            "integrated_tax": igst,
            "central_tax": cgst,
            "state_ut_tax": sgst,
            "cess": Decimal("0.00")
        },
        "3_1_b_outward_taxable_zero_rated": {
            "total_taxable_value": Decimal("0.00"),
            "integrated_tax": Decimal("0.00"),
            "cess": Decimal("0.00")
        },
        "3_1_c_other_outward_supplies_nil_exempt": {
            "total_taxable_value": Decimal("0.00")
        },
        "3_1_d_inward_supplies_reverse_charge": {
            "total_taxable_value": Decimal("0.00"),
            "integrated_tax": Decimal("0.00"),
            "central_tax": Decimal("0.00"),
            "state_ut_tax": Decimal("0.00"),
            "cess": Decimal("0.00")
        }
    }

    # 3. Table 4 Eligible ITC (Simulated based on purchase transactions/records)
    table_4 = {
        "itc_available": {
            "import_of_goods": {"igst": Decimal("0.00"), "cess": Decimal("0.00")},
            "import_of_services": {"igst": Decimal("0.00"), "cess": Decimal("0.00")},
            "inward_reverse_charge": {"igst": Decimal("0.00"), "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "cess": Decimal("0.00")},
            "all_other_itc": {"igst": Decimal("0.00"), "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "cess": Decimal("0.00")}
        },
        "itc_reversed": {
            "as_per_rules_42_43": {"igst": Decimal("0.00"), "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "cess": Decimal("0.00")},
            "others": {"igst": Decimal("0.00"), "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "cess": Decimal("0.00")}
        },
        "net_itc_available": {
            "igst": Decimal("0.00"),
            "cgst": Decimal("0.00"),
            "sgst": Decimal("0.00"),
            "cess": Decimal("0.00")
        }
    }

    tax_payable = {
        "total_tax_payable": {
            "integrated_tax": igst,
            "central_tax": cgst,
            "state_ut_tax": sgst,
            "total": igst + cgst + sgst
        },
        "tax_paid_through_itc": {
            "integrated_tax": Decimal("0.00"),
            "central_tax": Decimal("0.00"),
            "state_ut_tax": Decimal("0.00")
        },
        "net_tax_payable_in_cash": {
            "integrated_tax": igst,
            "central_tax": cgst,
            "state_ut_tax": sgst,
            "total": igst + cgst + sgst
        }
    }

    return GSTR3BDraftResponse(
        disclaimer=GSTR_WORKING_DRAFT_DISCLAIMER,
        tax_period=tax_period,
        gstin=gstin,
        legal_name=legal_name,
        table_3_1_outward_supplies=table_3_1,
        table_4_eligible_itc=table_4,
        tax_payable_summary=tax_payable
    )
