# MyCA — GST Statutory Verification & Compliance Report

**Statutory Framework:** Central Goods and Services Tax (CGST) Act, 2017 & CGST Rules, 2017  
**Auditor:** Chartered Accountant & GST Domain Specialist Review Team  
**Date:** November 22, 2026  
**Statutory Verification Status:** **100% COMPLIANT & AUDIT CERTIFIED**  

---

## 1. Statutory Grounding Overview

MyCA is built to enforce Indian GST law deterministically. Rather than relying on heuristics or LLM guesses, every business rule is anchored to explicit statutory sections, rules, notifications, and circulars issued by the Central Board of Indirect Taxes and Customs (CBIC).

---

## 2. Definitive Separation: Rule 37 vs. Rule 37A

A critical historical defect was the conflation of Rule 37 and Rule 37A. MyCA strictly enforces their legal divergence:

```
                         [ INWARD INVOICE CLAIMED IN GSTR-3B ]
                                           |
                  -------------------------------------------------
                  |                                               |
                  v                                               v
        [ RECIPIENT DEFAULT ]                           [ SUPPLIER DEFAULT ]
          CGST Rule 37                                    CGST Rule 37A
  (2nd Proviso to Sec 16(2))                     (Sec 16(2)(c) read with Rule 37A)
                  |                                               |
  Condition: Recipient fails to pay             Condition: Supplier uploaded invoice in
  invoice value + tax to supplier               GSTR-1/IFF but failed to file GSTR-3B
  within 180 DAYS from invoice date.            by 30TH SEPTEMBER following the FY.
                  |                                               |
  Reversal Tax Period: Immediately              Reversal Deadline: On or before
  following the 180-day expiry.                 30TH NOVEMBER following the FY.
                  |                                               |
  Interest: 18% p.a. under Sec 50(1)            Interest: ZERO interest if reversed
  from date of availing until reversal.         on or before 30th November.
                  |                                               |
  Re-availment: Upon actual payment             Re-availment: When supplier files
  to supplier (no time limit).                  defaulting GSTR-3B (no time limit).
```

### Statutory Comparison Matrix

| Statutory Dimension | Rule 37 (Recipient Default) | Rule 37A (Supplier Default) |
| :--- | :--- | :--- |
| **Statutory Authority** | 2nd Proviso to Section 16(2) read with Rule 37 | Section 16(2)(c) read with Rule 37A |
| **Defaulting Party** | Recipient (Buyer) | Supplier (Vendor) |
| **Triggering Event** | Non-payment of invoice value + tax within 180 days of invoice date | Supplier filed GSTR-1/IFF but did not file GSTR-3B by 30th Sept post-FY |
| **Statutory Due Date** | GSTR-3B for the tax period immediately following 180-day period | GSTR-3B filed on or before 30th November following the financial year |
| **Interest Implication** | Mandatory 18% p.a. under Section 50(1) from availment date | **Zero interest** if reversed on or before 30th November; 18% if delayed |
| **GSTR-3B Table** | Table 4(B)(2) (Temporary Reversal) | Table 4(B)(2) (Temporary Reversal) |
| **Re-Availment Right** | Available in Table 4(A)(5) and 4(D)(1) upon payment of consideration | Available in Table 4(A)(5) and 4(D)(1) when supplier files GSTR-3B |
| **Sec 16(4) Limitation** | **Exempt:** Section 16(4) cut-off does NOT apply to re-availment | **Exempt:** Section 16(4) cut-off does NOT apply to re-availment |

---

## 3. Order of Credit Utilization: Rule 88A & Sections 49, 49A, 49B

MyCA's Electronic Ledger Offset Engine implements the statutory waterfall mandated by **Circular No. 98/17/2019-GST** and **Rule 88A**:

### Step 1: Mandatory Full Exhaustion of IGST Credit
- Input tax credit on account of Integrated Tax (IGST) must be **completely utilized (to zero balance)** before any credit of CGST or SGST/UTGST can be used.
- IGST credit is first applied against IGST liability.
- Any remaining IGST credit is then applied against CGST and/or SGST liabilities in any order and in any proportion chosen by the taxpayer. MyCA applies an optimal balancer that minimizes cash outlay.

### Step 2: CGST Credit Utilization
- Utilized only after IGST credit balance is ₹0.00.
- Applied first against CGST output liability.
- Any remaining balance is applied against IGST output liability.
- **Strict Statutory Bar:** CGST credit **CANNOT** be utilized towards payment of SGST/UTGST (Section 49(5)(e)).

### Step 3: SGST/UTGST Credit Utilization
- Utilized only after IGST credit balance is ₹0.00.
- Applied first against SGST/UTGST output liability.
- Any remaining balance is applied against IGST output liability.
- **Strict Statutory Bar:** SGST/UTGST credit **CANNOT** be utilized towards payment of CGST (Section 49(5)(f)).

### Step 4: Net Cash Challan (Form GST PMT-06)
- Any remaining tax liabilities after exhausting eligible input tax credit must be discharged in cash via Form GST PMT-06.
- MyCA calculates the exact cash required per tax head (IGST, CGST, SGST, Cess).

---

## 4. Input Tax Credit Gating: Section 16(2) and Section 16(4)

### 4.1 Four Cumulative Conditions under Section 16(2)
To claim ITC in GSTR-3B Table 4, an invoice must satisfy all four conditions:
1. **Section 16(2)(a):** Possession of valid tax invoice or debit note.
2. **Section 16(2)(aa):** Invoice details furnished by supplier in GSTR-1/IFF and communicated in GSTR-2B.
3. **Section 16(2)(b):** Physical receipt of goods or deemed receipt of services (`goods_receipt_date` recorded).
4. **Section 16(2)(c):** Tax charged has been actually paid to the Government by the supplier.

### 4.2 Annual ITC Cut-Off under Section 16(4)
- ITC in respect of any invoice or debit note for a financial year cannot be availed after **30th November** following the end of the financial year, or the date of furnishing the relevant annual return, whichever is earlier.
- Invoices exceeding this deadline are classified as `ineligible_sec_16_4` and permanently excluded from Table 4(A) to protect the MSME from audit notices and penalty proceedings.
