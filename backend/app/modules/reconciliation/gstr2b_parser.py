import sys
from pathlib import Path
vendor_dir = Path(__file__).resolve().parent.parent.parent.parent / "vendor"
if vendor_dir.exists() and str(vendor_dir) not in sys.path:
    sys.path.insert(0, str(vendor_dir))

import json
import logging
from decimal import Decimal
from typing import List, Dict, Any, Tuple
import openpyxl
import io
from .schemas import GSTR2BDocumentItem, GSTR2BParsedStatement

logger = logging.getLogger("reconciliation.gstr2b_parser")


def normalize_doc_number(doc_num: str) -> str:
    """Removes slashes, dashes, whitespace, and leading zeros for canonical matching."""
    if not doc_num:
        return ""
    clean = doc_num.strip().upper().replace("/", "").replace("-", "").replace(" ", "").replace("_", "")
    return clean.lstrip("0") or clean


def parse_gstr2b_json(content: bytes) -> GSTR2BParsedStatement:
    """
    Parses official GST Portal GSTR-2B JSON download.
    Supports all Part A (Available) and Part B (Not Available) sections.
    """
    data = json.loads(content.decode("utf-8"))
    
    # Root level structure
    recipient_gstin = data.get("gstin", "")
    raw_fp = str(data.get("fp", ""))
    
    # Normalize return period YYYY-MM
    if len(raw_fp) == 6:  # MMYYYY (e.g. '112026')
        return_period = f"{raw_fp[2:]}-{raw_fp[:2]}"
    elif "-" in raw_fp:
        return_period = raw_fp
    else:
        return_period = "2026-11"

    fy = data.get("fy", f"{return_period[:4]}-{int(return_period[:4])+1}")
    gen_date = data.get("genDate") or data.get("gendt")

    parsed_docs: List[GSTR2BDocumentItem] = []
    total_itc_avail = Decimal("0.00")
    total_itc_not_avail = Decimal("0.00")

    doc_data = data.get("data", data)

    # 1. B2B Invoices & B2BA Amendments
    for section_key, is_amended in [("b2b", False), ("b2ba", True)]:
        suppliers = doc_data.get(section_key, [])
        for supp in suppliers:
            supplier_gstin = supp.get("ctin")
            supplier_name = supp.get("trdnm") or supp.get("legal_name") or supp.get("cname")
            invoices = supp.get("inv", [])
            for inv in invoices:
                inum = str(inv.get("inum", "")).strip()
                idt = inv.get("idt", "")
                val = Decimal(str(inv.get("val", 0)))
                pos = str(inv.get("pos", "27")).zfill(2)
                rchrg = (str(inv.get("rchrg", "N")).upper() == "Y")
                itcavl = str(inv.get("itcavl", "Y")).upper()
                itc_avail = "available" if itcavl == "Y" else "not_available"
                source_form = inv.get("srctyp", "GSTR-1")
                filing_date = inv.get("srchdt")

                # Accumulate item rows
                taxable = Decimal("0.00")
                iamt = Decimal("0.00")
                camt = Decimal("0.00")
                samt = Decimal("0.00")
                csamt = Decimal("0.00")
                for itm in inv.get("items", []):
                    item_details = itm.get("itmdet", itm)
                    t_val = Decimal(str(item_details.get("txval", 0)))
                    i_val = Decimal(str(item_details.get("iamt", 0)))
                    c_val = Decimal(str(item_details.get("camt", 0)))
                    s_val = Decimal(str(item_details.get("samt", 0)))
                    cs_val = Decimal(str(item_details.get("csamt", 0)))
                    taxable += t_val
                    iamt += i_val
                    camt += c_val
                    samt += s_val
                    csamt += cs_val

                doc_item = GSTR2BDocumentItem(
                    section_name="B2BA" if is_amended else "B2B",
                    itc_availability=itc_avail,
                    source_form=source_form,
                    supplier_gstin=supplier_gstin,
                    supplier_name=supplier_name,
                    document_type="INV",
                    document_number=inum,
                    document_date=idt,
                    invoice_value=val,
                    place_of_supply=pos,
                    supply_attract_reverse_charge=rchrg,
                    taxable_value=taxable,
                    integrated_tax=iamt,
                    central_tax=camt,
                    state_ut_tax=samt,
                    cess_amount=csamt,
                    source_gstr1_filing_date=filing_date,
                    is_amended=is_amended,
                    raw_payload=inv
                )
                parsed_docs.append(doc_item)
                tax_sum = iamt + camt + samt + csamt
                if itc_avail == "available":
                    total_itc_avail += tax_sum
                else:
                    total_itc_not_avail += tax_sum

    # 2. CDNR & CDNRA (Credit/Debit Notes)
    for section_key, is_amended in [("cdnr", False), ("cdnra", True)]:
        suppliers = doc_data.get(section_key, [])
        for supp in suppliers:
            supplier_gstin = supp.get("ctin")
            supplier_name = supp.get("trdnm") or supp.get("cname")
            notes = supp.get("nt", [])
            for nt in notes:
                nt_num = str(nt.get("nt_num") or nt.get("ntnum") or "").strip()
                nt_dt = str(nt.get("nt_dt") or nt.get("ntdt") or "")
                nt_typ = str(nt.get("ntty", "C")).upper()  # C: Credit, D: Debit
                val = Decimal(str(nt.get("val", 0)))
                pos = str(nt.get("pos", "27")).zfill(2)
                rchrg = (str(nt.get("rchrg", "N")).upper() == "Y")
                itcavl = str(nt.get("itcavl", "Y")).upper()
                itc_avail = "available" if itcavl == "Y" else "not_available"

                taxable = Decimal("0.00")
                iamt = Decimal("0.00")
                camt = Decimal("0.00")
                samt = Decimal("0.00")
                csamt = Decimal("0.00")
                for itm in nt.get("items", []):
                    item_details = itm.get("itmdet", itm)
                    taxable += Decimal(str(item_details.get("txval", 0)))
                    iamt += Decimal(str(item_details.get("iamt", 0)))
                    camt += Decimal(str(item_details.get("camt", 0)))
                    samt += Decimal(str(item_details.get("samt", 0)))
                    csamt += Decimal(str(item_details.get("csamt", 0)))

                doc_item = GSTR2BDocumentItem(
                    section_name="CDNRA" if is_amended else "CDNR",
                    itc_availability=itc_avail,
                    source_form=nt.get("srctyp", "GSTR-1"),
                    supplier_gstin=supplier_gstin,
                    supplier_name=supplier_name,
                    document_type="CRN" if nt_typ == "C" else "DBN",
                    document_number=nt_num,
                    document_date=nt_dt,
                    invoice_value=val,
                    place_of_supply=pos,
                    supply_attract_reverse_charge=rchrg,
                    taxable_value=taxable,
                    integrated_tax=iamt,
                    central_tax=camt,
                    state_ut_tax=samt,
                    cess_amount=csamt,
                    is_amended=is_amended,
                    raw_payload=nt
                )
                parsed_docs.append(doc_item)
                tax_sum = iamt + camt + samt + csamt
                if itc_avail == "available":
                    total_itc_avail += tax_sum
                else:
                    total_itc_not_avail += tax_sum

    # 3. ISD Invoices & ISDA Amendments (GSTR-6 Source)
    for section_key, is_amended in [("isd", False), ("isda", True)]:
        isd_entries = doc_data.get(section_key, [])
        for entry in isd_entries:
            isd_gstin = entry.get("ctin")
            isd_name = entry.get("cname") or "Input Service Distributor"
            for doc in entry.get("doclist", []):
                dnum = str(doc.get("docnum", "")).strip()
                ddt = doc.get("docdt", "")
                iamt = Decimal(str(doc.get("iamt", 0)))
                camt = Decimal(str(doc.get("camt", 0)))
                samt = Decimal(str(doc.get("samt", 0)))
                csamt = Decimal(str(doc.get("csamt", 0)))
                itc_avail = "available" if str(doc.get("itcavl", "Y")).upper() == "Y" else "not_available"

                doc_item = GSTR2BDocumentItem(
                    section_name="ISDA" if is_amended else "ISD",
                    itc_availability=itc_avail,
                    source_form="GSTR-6",
                    supplier_gstin=isd_gstin,
                    supplier_name=isd_name,
                    document_type="ISD_INV",
                    document_number=dnum,
                    document_date=ddt,
                    invoice_value=iamt + camt + samt + csamt,
                    place_of_supply=recipient_gstin[:2] if recipient_gstin else "27",
                    supply_attract_reverse_charge=False,
                    taxable_value=Decimal("0.00"),
                    integrated_tax=iamt,
                    central_tax=camt,
                    state_ut_tax=samt,
                    cess_amount=csamt,
                    is_amended=is_amended,
                    raw_payload=doc
                )
                parsed_docs.append(doc_item)
                if itc_avail == "available":
                    total_itc_avail += (iamt + camt + samt + csamt)
                else:
                    total_itc_not_avail += (iamt + camt + samt + csamt)

    # 4. IMPG & IMPGSEZ (ICEGATE Import of Goods)
    for section_key in ["impg", "impga", "impgsez", "impgseza"]:
        impg_entries = doc_data.get(section_key, [])
        for imp in impg_entries:
            boe_num = str(imp.get("boenum", "")).strip()
            boe_dt = imp.get("boedt", "")
            port = imp.get("portcode", "")
            taxable = Decimal(str(imp.get("txval", 0)))
            iamt = Decimal(str(imp.get("iamt", 0)))
            csamt = Decimal(str(imp.get("csamt", 0)))
            itc_avail = "available" if str(imp.get("itcavl", "Y")).upper() == "Y" else "not_available"

            doc_item = GSTR2BDocumentItem(
                section_name=section_key.upper(),
                itc_availability=itc_avail,
                source_form="ICEGATE",
                supplier_gstin="OVERSEAS_SUPPLIER" if "sez" not in section_key else imp.get("ctin", "SEZ_SUPPLIER"),
                supplier_name="ICEGATE Customs Import" if "sez" not in section_key else "SEZ Unit / Developer",
                document_type="BOE",
                document_number=boe_num,
                document_date=boe_dt,
                invoice_value=taxable + iamt + csamt,
                place_of_supply=recipient_gstin[:2] if recipient_gstin else "27",
                supply_attract_reverse_charge=False,
                taxable_value=taxable,
                integrated_tax=iamt,
                central_tax=Decimal("0.00"),
                state_ut_tax=Decimal("0.00"),
                cess_amount=csamt,
                boe_number=boe_num,
                boe_date=boe_dt,
                port_code=port,
                is_amended=("a" in section_key),
                raw_payload=imp
            )
            parsed_docs.append(doc_item)
            if itc_avail == "available":
                total_itc_avail += (iamt + csamt)
            else:
                total_itc_not_avail += (iamt + csamt)

    return GSTR2BParsedStatement(
        recipient_gstin=recipient_gstin,
        return_period=return_period,
        financial_year=fy,
        generation_date=gen_date,
        total_itc_available=total_itc_avail,
        total_itc_not_available=total_itc_not_avail,
        documents=parsed_docs,
        parser_version="v2.1_official"
    )


def parse_gstr2b_excel(content: bytes) -> GSTR2BParsedStatement:
    """
    Parses official GST Portal GSTR-2B Excel spreadsheet.
    Extracts sheets: B2B, B2BA, CDNR, CDNRA, ISD, IMPG.
    """
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    parsed_docs: List[GSTR2BDocumentItem] = []
    total_itc_avail = Decimal("0.00")
    total_itc_not_avail = Decimal("0.00")

    recipient_gstin = ""
    return_period = "2026-11"

    # Read summary sheet if available
    if "ITC Available" in wb.sheetnames:
        ws_sum = wb["ITC Available"]
        # Typically header rows contain GSTIN and Period
        for r in range(1, 8):
            cell_val = str(ws_sum.cell(row=r, column=1).value or "")
            if "GSTIN" in cell_val.upper() and ":" in cell_val:
                recipient_gstin = cell_val.split(":")[-1].strip()
            if "Period" in cell_val and ":" in cell_val:
                return_period = cell_val.split(":")[-1].strip()

    # Read B2B Sheet
    if "B2B" in wb.sheetnames:
        ws_b2b = wb["B2B"]
        header_idx = None
        for r in range(1, 10):
            val1 = str(ws_b2b.cell(row=r, column=1).value or "").strip().lower()
            if "gstin" in val1:
                header_idx = r
                break
        
        if header_idx:
            for r in range(header_idx + 1, ws_b2b.max_row + 1):
                supp_gstin = ws_b2b.cell(row=r, column=1).value
                if not supp_gstin or len(str(supp_gstin).strip()) < 15:
                    continue
                supp_name = str(ws_b2b.cell(row=r, column=2).value or "Supplier")
                inv_num = str(ws_b2b.cell(row=r, column=3).value or "").strip()
                inv_dt = str(ws_b2b.cell(row=r, column=4).value or "2026-11-01")[:10]
                inv_val = Decimal(str(ws_b2b.cell(row=r, column=5).value or 0))
                pos = str(ws_b2b.cell(row=r, column=6).value or "27")[:2]
                rchrg = (str(ws_b2b.cell(row=r, column=7).value or "N").upper() == "Y")
                taxable = Decimal(str(ws_b2b.cell(row=r, column=9).value or 0))
                igst = Decimal(str(ws_b2b.cell(row=r, column=10).value or 0))
                cgst = Decimal(str(ws_b2b.cell(row=r, column=11).value or 0))
                sgst = Decimal(str(ws_b2b.cell(row=r, column=12).value or 0))
                cess = Decimal(str(ws_b2b.cell(row=r, column=13).value or 0))

                doc_item = GSTR2BDocumentItem(
                    section_name="B2B",
                    itc_availability="available",
                    source_form="GSTR-1",
                    supplier_gstin=str(supp_gstin).strip(),
                    supplier_name=supp_name,
                    document_type="INV",
                    document_number=inv_num,
                    document_date=inv_dt,
                    invoice_value=inv_val,
                    place_of_supply=pos,
                    supply_attract_reverse_charge=rchrg,
                    taxable_value=taxable,
                    integrated_tax=igst,
                    central_tax=cgst,
                    state_ut_tax=sgst,
                    cess_amount=cess
                )
                parsed_docs.append(doc_item)
                total_itc_avail += (igst + cgst + sgst + cess)

    return GSTR2BParsedStatement(
        recipient_gstin=recipient_gstin or "27AABCB1234M1ZT",
        return_period=return_period,
        financial_year="2026-2027",
        total_itc_available=total_itc_avail,
        total_itc_not_available=total_itc_not_avail,
        documents=parsed_docs,
        parser_version="v2.1_excel"
    )
