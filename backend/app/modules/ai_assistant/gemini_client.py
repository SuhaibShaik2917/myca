import os
from datetime import datetime, timezone
from typing import List, Optional
from app.modules.ai_assistant.schemas import (
    ChatMessageIn, ChatResponseOut, GroundingContextSummary
)

def build_system_prompt(ctx: Optional[GroundingContextSummary]) -> str:
    grounding_info = ""
    if ctx:
        grounding_info = f"""
TENANT REAL-TIME STATUTORY & FINANCIAL CONTEXT:
- Organization Legal Name: {ctx.org_legal_name}
- GSTIN: {ctx.gstin or 'Not Configured'}
- Return Period: {ctx.latest_return_period or 'Current'}
- Net Tax Liability (Table 6.1): INR {ctx.net_tax_liability or '0.00'}
- Eligible ITC (Table 4A): INR {ctx.eligible_itc or '0.00'}
- Pending Reconciliation Invoices: {ctx.pending_vendor_invoices}
- MSME Financial Readiness Index: {ctx.msme_readiness_score or 'Not evaluated'}/900
- Top Matched Scheme: {ctx.top_recommended_scheme or 'CGTMSE'}

STATUTORY GOVERNANCE RULES:
1. Rule 37 vs Rule 37A:
   - Rule 37: If recipient fails to pay invoice + tax to supplier within 180 days, recipient must proportionately reverse ITC in GSTR-3B with Section 50 interest.
   - Rule 37A: If supplier fails to file GSTR-3B by 30th Sept following the FY, recipient must reverse ITC by 30th Nov.
2. Rule 88A: IGST credit must be fully exhausted against IGST, CGST, SGST before CGST or SGST credit can be utilized. CGST credit cannot be offset against SGST liability and vice versa.
3. Section 16(4): ITC cut-off date is 30th November following the end of the financial year.
"""

    return f"""You are MyCA AI Financial & Statutory Copilot, an expert Chartered Accountant AI for Indian MSMEs.
You provide precise statutory advice cite relevant provisions of the CGST Act 2017, CGST Rules 2017, and RBI guidelines.
Never give vague or contradictory advice. Always maintain statutory correctness.
{grounding_info}
"""

def generate_fallback_response(query: str, ctx: Optional[GroundingContextSummary]) -> str:
    q = query.lower()
    org_name = ctx.org_legal_name if ctx else "your business"
    gstin = ctx.gstin if ctx and ctx.gstin else "your registered GSTIN"
    net_tax = f"₹{ctx.net_tax_liability}" if ctx and ctx.net_tax_liability else "₹0.00"

    if "rule 37a" in q or "37a" in q:
        return (
            f"**Statutory Guidance on CGST Rule 37A:**\n\n"
            f"Under **Rule 37A of the CGST Rules, 2017**, if your supplier furnished invoice details in GSTR-1/IFF but failed to file their corresponding **GSTR-3B return** for the tax period by **30th September** following the financial year in which you claimed ITC, you are required to reverse the availed ITC in your GSTR-3B on or before **30th November**.\n\n"
            f"**Key Protections & Re-availment:**\n"
            f"- If reversed on or before 30th November, no interest under Section 50 is payable.\n"
            f"- Once the supplier subsequently files their defaulting GSTR-3B and discharges tax, you are statutorily entitled to **re-claim** the reversed ITC under Rule 37A without any time limitation under Section 16(4)."
        )

    if "rule 37" in q or "180 day" in q or "180-day" in q or "supplier payment" in q:
        return (
            f"**Statutory Guidance on CGST Rule 37 (180-Day Supplier Payment Condition):**\n\n"
            f"Under the second proviso to **Section 16(2) of the CGST Act** read with **Rule 37**, a registered recipient must pay the supplier the invoice value plus tax within **180 days** from the invoice date.\n\n"
            f"- **Mandatory Reversal:** If unpaid within 180 days, you must proportionately reverse the availed ITC in Table 4(B)(2) of your GSTR-3B for the tax period immediately following the 180-day expiry.\n"
            f"- **Interest Liability:** Interest at 18% p.a. under Section 50(1) is applicable from the date of availing ITC until the date of reversal.\n"
            f"- **Re-availment:** When you subsequently pay the supplier (even after years), you can re-avail the reversed ITC in full under Section 16(2) third proviso; the Section 16(4) 30th November cut-off does NOT apply to re-availment."
        )

    if "rule 88a" in q or "offset" in q or "utilization" in q or "pmt-06" in q or "set off" in q:
        return (
            f"**Statutory Credit Utilization Order under Rule 88A & Section 49:**\n\n"
            f"MyCA's Electronic Ledger Offset Engine implements the strict statutory waterfall mandated by **Rule 88A** and **Sections 49, 49A, and 49B**:\n\n"
            f"1. **IGST Credit Priority:** IGST credit balance must be **100% exhausted** first (offsetting IGST liability, then CGST and/or SGST in any chosen order).\n"
            f"2. **CGST Credit:** Utilized only after IGST credit is zero. Offsets CGST liability first, then IGST. **Cannot offset SGST.**\n"
            f"3. **SGST Credit:** Utilized only after IGST credit is zero. Offsets SGST liability first, then IGST. **Cannot offset CGST.**\n\n"
            f"For {org_name}, the latest GSTR-3B draft reflects net cash liability of **{net_tax}** payable via PMT-06 challan."
        )

    if "score" in q or "credit" in q or "dscr" in q or "readiness" in q or "loan" in q or "dossier" in q:
        score_val = ctx.msme_readiness_score if ctx and ctx.msme_readiness_score else 680
        top_scheme = ctx.top_recommended_scheme if ctx else "CGTMSE Collateral-Free Credit Guarantee"
        return (
            f"**MSME Financial Readiness & Credit Builder Analysis for {org_name}:**\n\n"
            f"- **Current Readiness Score:** **{score_val}/900**\n"
            f"- **Top Matched Facility:** {top_scheme}\n\n"
            f"**Four Readiness Pillars Evaluated:**\n"
            f"1. **Cash Flow Regularity:** Based on consecutive months of verified net inflows.\n"
            f"2. **DSCR Proxy:** Net operating cash flows relative to debt service obligations (target benchmark >= 1.5x).\n"
            f"3. **Operating Profit Margin:** Demonstrating pricing power and operational cushion.\n"
            f"4. **Statutory GST Provenance:** Clean reconciliation without audit freezes or Rule 37 defaults.\n\n"
            f"*Regulatory Notice: The MyCA score is an internal analytical metric and not a credit bureau score (CIBIL/Equifax). You can download your structured Loan Dossier in the Credit Builder tab.*"
        )

    if "scheme" in q or "mudra" in q or "cgtmse" in q or "pmegp" in q or "svanidhi" in q or "zed" in q:
        return (
            f"**Government Scheme Advisory for {org_name} (GSTIN: {gstin}):**\n\n"
            f"Based on your MSME profile, you are evaluated against 6 major central government schemes:\n\n"
            f"1. **CGTMSE:** Collateral-free loans up to ₹2 Crore with 75-85% credit guarantee for Micro & Small enterprises.\n"
            f"2. **PMMY (MUDRA):** Loans up to ₹10 Lakh across Shishu, Kishore, and Tarun tiers with zero processing fee.\n"
            f"3. **PMEGP:** 15% to 35% margin money government subsidy on manufacturing projects up to ₹50L.\n"
            f"4. **PM-SVANidhi:** Working capital micro-loans up to ₹50,000 with 7% interest subsidy.\n"
            f"5. **Stand-Up India:** Greenfield bank financing between ₹10L and ₹100L for SC/ST and Women entrepreneurs.\n"
            f"6. **MSME ZED Scheme:** Up to 80% subsidy on zero-defect manufacturing certification costs.\n\n"
            f"Visit the **Scheme Navigator** tab to run full automated eligibility checks and view your application checklists."
        )

    if "gstr-3b" in q or "gstr3b" in q or "table 4" in q or "table 3.1" in q or "liability" in q:
        return (
            f"**GSTR-3B Preparation Summary for {org_name}:**\n\n"
            f"- **Filing Period:** {ctx.latest_return_period if ctx else 'Current'}\n"
            f"- **Net Cash Payable (Table 6.1):** **{net_tax}**\n"
            f"- **Eligible Inward ITC (Table 4A):** **₹{ctx.eligible_itc if ctx else '0.00'}**\n"
            f"- **Pending Reconciliations:** {ctx.pending_vendor_invoices if ctx else 0} invoices\n\n"
            f"**Statutory Workflow in MyCA:**\n"
            f"1. All approved invoices automatically populate Table 3.1 (Outward Supplies).\n"
            f"2. Reconciled 2B documents populate Table 4 (Eligible ITC).\n"
            f"3. Rule 88A offset engine calculates optimal credit utilization and PMT-06 cash challan.\n"
            f"4. Two-step Maker-Checker review ensures no unauthorized filings before GSTN Sandbox submission."
        )

    # General Assistant response grounded in tenant context
    return (
        f"Hello! I am your **MyCA AI Copilot** for {org_name} (GSTIN: {gstin}).\n\n"
        f"**Your Current Financial & Statutory Snapshot:**\n"
        f"- **Latest Return Period:** {ctx.latest_return_period if ctx else 'Current'}\n"
        f"- **Net Tax Liability:** {net_tax}\n"
        f"- **Unbooked/Pending Invoices:** {ctx.pending_vendor_invoices if ctx else 0}\n"
        f"- **MSME Readiness Score:** {ctx.msme_readiness_score or 680}/900\n\n"
        f"How can I assist you today? You can ask about:\n"
        f"- **GST Compliance:** Rule 37 (180-day supplier payment), Rule 37A (supplier non-filing), Rule 88A (credit set-off).\n"
        f"- **GSTR-3B Filing:** Table 3.1 liability, Table 4 ITC, Maker-Checker review.\n"
        f"- **Credit Builder:** DSCR proxy, Bank statement cash flow analysis, Loan Dossier.\n"
        f"- **Scheme Navigator:** Eligibility for CGTMSE, MUDRA, PMEGP, PM-SVANidhi, and ZED."
    )

def answer_ai_query(
    messages: List[ChatMessageIn],
    ctx: Optional[GroundingContextSummary]
) -> ChatResponseOut:
    """
    Executes AI inference. Uses Gemini API if API key is provided;
    falls back cleanly to deterministic GST domain engine if key is absent or on network error.
    """
    user_query = messages[-1].content if messages else "Hello"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    gemini_key = os.environ.get("GEMINI_API_KEY")

    if gemini_key:
        try:
            # Attempt official google-genai SDK if installed
            from google import genai
            client = genai.Client(api_key=gemini_key)
            system_prompt = build_system_prompt(ctx)
            contents = [f"{m.role.upper()}: {m.content}" for m in messages]
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents="\n".join(contents),
                config={"system_instruction": system_prompt}
            )
            if response and response.text:
                return ChatResponseOut(
                    reply=response.text,
                    grounding_applied=True,
                    grounding_context=ctx,
                    model_used="gemini-2.5-flash",
                    timestamp=now_str
                )
        except Exception:
            # Clean fallback to deterministic engine on any failure
            pass

    fallback_text = generate_fallback_response(user_query, ctx)
    return ChatResponseOut(
        reply=fallback_text,
        grounding_applied=True,
        grounding_context=ctx,
        model_used="myca-gst-intelligence-engine-v2",
        timestamp=now_str
    )
