import os
import json
import logging
from typing import Dict, Any, Optional
from decimal import Decimal
from pydantic import BaseModel
from app.config import settings

logger = logging.getLogger("invoice_brain.ocr")

class ExtractedLineItem(BaseModel):
    item_description: str
    hsn_sac_code: Optional[str] = None
    quantity: float = 1.0
    unit_price: float
    taxable_amount: float
    gst_rate: float = 18.0
    cgst_amount: float = 0.0
    sgst_amount: float = 0.0
    igst_amount: float = 0.0
    total_amount: float

class ExtractedInvoiceData(BaseModel):
    invoice_number: str
    invoice_date: str  # YYYY-MM-DD
    due_date: Optional[str] = None
    counterparty_name: str
    counterparty_gstin: Optional[str] = None
    total_taxable_amount: float
    cgst_amount: float = 0.0
    sgst_amount: float = 0.0
    igst_amount: float = 0.0
    total_amount: float
    confidence_score: float = 0.95
    line_items: list[ExtractedLineItem] = []
    raw_text: Optional[str] = None

INVOICE_EXTRACTION_PROMPT = """
You are an expert Indian GST tax invoice data extraction agent.
Carefully extract all fields from this invoice document according to Indian GST standards.
Extract the following:
1. invoice_number: The unique identifier of the invoice.
2. invoice_date: Date in YYYY-MM-DD format.
3. due_date: Optional due date in YYYY-MM-DD format.
4. counterparty_name: Name of the supplier/vendor or customer.
5. counterparty_gstin: 15-character GST identification number if present.
6. line_items: Each item with description, HSN/SAC code, quantity, unit price, taxable amount, GST rate, CGST, SGST, IGST, total amount.
7. Total financial amounts: total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount.
8. confidence_score: Confidence score from 0.0 to 1.0.

Ensure numeric precision is maintained. Return ONLY clean structured JSON adhering to the schema.
"""

def extract_invoice_with_gemini(file_path: str, mime_type: str) -> ExtractedInvoiceData:
    """
    Extracts structured invoice information using Google Gemini 2.0 Flash Vision.
    Falls back to deterministic parser ONLY in non-production development/test modes.
    PROHIBITED IN PRODUCTION: If ENVIRONMENT == 'production', mock fallback is strictly forbidden.
    """
    api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    if not api_key:
        if settings.ENVIRONMENT == "production":
            raise RuntimeError("CRITICAL PRODUCTION INTEGRITY ERROR: GEMINI_API_KEY is not configured. Mock OCR fallback is strictly prohibited in production.")
        logger.info("GEMINI_API_KEY not configured. Using deterministic OCR fallback parser for dev/testing.")
        return extract_invoice_mock(file_path, mime_type)

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        file_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=[file_part, INVOICE_EXTRACTION_PROMPT],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ExtractedInvoiceData,
                temperature=0.1,
            )
        )

        extracted = ExtractedInvoiceData.model_validate_json(response.text)
        return extracted

    except Exception as e:
        if settings.ENVIRONMENT == "production":
            raise RuntimeError(f"CRITICAL: Gemini Vision OCR extraction failed in production: {e}") from e
        logger.warning(f"Gemini API extraction failed ({e}). Falling back to deterministic fallback.")
        return extract_invoice_mock(file_path, mime_type)

def extract_invoice_mock(file_path: str, mime_type: str) -> ExtractedInvoiceData:
    """
    Deterministic mock extractor for unit tests, offline staging, and CI environments.
    Extracts realistic, GST-compliant invoice data with consistent line items.
    """
    # Deterministic generation based on file name or size
    file_stat = os.stat(file_path)
    file_seed = file_stat.st_size % 1000

    inv_num = f"INV-2026-{1000 + file_seed}"
    taxable_1 = 5000.00
    cgst_1 = 450.00
    sgst_1 = 450.00
    total_1 = 5900.00

    taxable_2 = 2500.00
    cgst_2 = 225.00
    sgst_2 = 225.00
    total_2 = 2950.00

    line_1 = ExtractedLineItem(
        item_description="Professional IT Consulting Services",
        hsn_sac_code="998311",
        quantity=1.0,
        unit_price=taxable_1,
        taxable_amount=taxable_1,
        gst_rate=18.0,
        cgst_amount=cgst_1,
        sgst_amount=sgst_1,
        igst_amount=0.0,
        total_amount=total_1
    )

    line_2 = ExtractedLineItem(
        item_description="Cloud Infrastructure Architecture Review",
        hsn_sac_code="998313",
        quantity=1.0,
        unit_price=taxable_2,
        taxable_amount=taxable_2,
        gst_rate=18.0,
        cgst_amount=cgst_2,
        sgst_amount=sgst_2,
        igst_amount=0.0,
        total_amount=total_2
    )

    total_taxable = taxable_1 + taxable_2
    total_cgst = cgst_1 + cgst_2
    total_sgst = sgst_1 + sgst_2
    grand_total = total_1 + total_2

    return ExtractedInvoiceData(
        invoice_number=inv_num,
        invoice_date="2026-11-10",
        due_date="2026-11-25",
        counterparty_name="Bharat Tech Solutions Private Limited",
        counterparty_gstin="27AABCB1234M1ZT",
        total_taxable_amount=total_taxable,
        cgst_amount=total_cgst,
        sgst_amount=total_sgst,
        igst_amount=0.0,
        total_amount=grand_total,
        confidence_score=0.98,
        line_items=[line_1, line_2],
        raw_text=f"TAX INVOICE {inv_num} Bharat Tech Solutions GSTIN 27AABCB1234M1Z2 Total Rs {grand_total}"
    )
