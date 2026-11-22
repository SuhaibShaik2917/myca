# MyCA — FINAL GST STATUTORY COMPLIANCE & ACCOUNTING INTEGRITY AUDIT

**Audit Date:** November 22, 2026  
**Auditor:** Principal GST Regulatory & Statutory Audit Specialist  
**Statutory Basis:** Central Goods and Services Tax (CGST) Act, 2017 & CGST Rules, 2017  
**Core Milestones Audited:** Milestone 2 (Inward Statutory Engine) & Milestone 3 (GSTR-3B Offset & Filing)

---

## 1. STATUTORY RESOLUTION MATRIX FOR HISTORICAL FINDINGS (M2.7.2.15)

| Finding Ref | Statutory Issue Identified | MyCA Codebase Resolution & Provenance | Verification Test |
| :--- | :--- | :--- | :--- |
| **F-01** | Conflation of Rule 37 (180-day unpaid) and Rule 37A (supplier non-filing) | **Strictly Separated:** `itc_engine.py` implements dedicated logic branches for Rule 37 vs Rule 37A with distinct deadlines, interest liabilities, and re-claim mechanics. | `test_milestone2_7_2_13_period_chronology_and_reversal_lineage.py` |
| **F-02** | October vs November 2B Chronology & Cut-off Dates | Formalized filing periods: GSTR-2B generation cut-off (14th of following month) strictly respects invoice issue dates and portal auto-population dates. | `test_future_dated_ledger_events_strictly_quarantined_from_verified_snapshot` |
| **F-03** | Future-dated supplier fixture quarantine | Isolated synthetic mock fixtures with future dates (`2026-02-15`, `2026-11-30`) into dedicated test sandboxes; production ledger rejects future dates. | `test_future_dated_ledger_events_strictly_quarantined_from_verified_snapshot` |
| **F-04** | Section 17(5)(ab) motor vehicle repair facts | Motor vehicle repair ITC is **not blindly blocked**. Fact gating evaluates seating capacity (>13) and transport purpose. Ambiguous cases route to `REVIEW_REQUIRED_FACTS_PENDING`. | `test_section_17_5_ab_vehicle_repair_fact_gating_and_ca_review` |
| **F-05** | Model A vs Model B Net ITC reconciliation discrepancy | Eliminated ₹27,000 double-deduction defect. Both Model A (direct net) and Model B (Circular 170 Gross auto-population) reconcile to exact **₹3,13,750.00**. | `test_unified_financial_report_reconciliation` |
| **F-06** | Software calculations vs CA statutory approval | Strict statutory separation: Software provides calculation & draft preparation; final statutory sign-off requires CA review with maker-checker locks. | `test_maker_checker_workflow_and_filing_gate` |

---

## 2. DEEP STATUTORY ANALYSIS: RULE 37 VS RULE 37A

### CGST Rule 37 — Reversal on Failure to Pay Supplier within 180 Days
- **Statutory Authority:** Second and Third Provisos to Section 16(2) read with Rule 37.
- **Trigger Event:** Recipient fails to pay the supplier the amount towards the value of supply along with tax within 180 days from the invoice date.
- **Reporting Mechanism:** Mandatory reversal in GSTR-3B Table 4(B)(2) for the tax period immediately following the expiry of 180 days.
- **Interest Liability:** 18% per annum under Section 50(1), calculated from the date of availing ITC until the date of reversal.
- **Re-availment Rights:** Recipient can re-avail the reversed ITC in GSTR-3B Table 4(A)(5) and Table 4(D)(1) upon subsequent payment to the supplier, **exempt from the Section 16(4) limitation period**.

### CGST Rule 37A — Reversal on Supplier's Failure to Furnish GSTR-3B
- **Statutory Authority:** Section 16(2)(c) read with Rule 37A (inserted vide Notification No. 26/2022-CT).
- **Trigger Event:** Supplier reported the invoice in GSTR-1/IFF (reflected in recipient's GSTR-2B) but failed to furnish their corresponding GSTR-3B return by 30th September following the end of the financial year.
- **Reporting Mechanism:** Recipient must reverse the availed ITC on or before 30th November following the end of the financial year in GSTR-3B Table 4(B)(2).
- **Interest Liability:**
  - **Zero Interest:** If reversed on or before 30th November.
  - **18% Interest:** If reversed after 30th November, computed from 1st December under Section 50.
- **Re-availment Rights:** Recipient can re-avail the reversed ITC in Table 4(A)(5) and Table 4(D)(1) when the supplier subsequently furnishes the pending GSTR-3B return.

### Dual-Trigger Protection in MyCA (`itc_engine.py`)
When an invoice simultaneously triggers both conditions (e.g. supplier unpaid after 180 days AND supplier has not filed GSTR-3B), MyCA's statutory risk engine:
1. Flags both statutory risks in the ITC assessment record.
2. Prioritizes the earlier statutory deadline (Rule 37: immediate subsequent month).
3. Enforces an immutable reversal cap: $\text{Reversal Amount} \le \text{Total Eligible ITC}$ to prevent double-deduction.

---

## 3. SECTION 17(5)(ab) FACTUAL GATING & MOTOR VEHICLE REPAIRS

Section 17(5)(ab) of the CGST Act blocks ITC on services of general insurance, servicing, repair, and maintenance in respect of motor vehicles, **EXCEPT**:
1. Where the motor vehicle is of a type specified in Section 17(5)(a) having approved seating capacity of more than 13 persons (including the driver).
2. Where the motor vehicle is used for taxable supplies of transportation of passengers, goods transport, or driving school training.

### MyCA Implementation
Rather than hard-blocking all automotive repair invoices:
- Invoices coded under SAC 998729 / 998714 are evaluated against the enterprise's vehicle registration data and business profile.
- If the vehicle seating capacity $\ge 13$ or the business category is passenger transportation: ITC is classified as **ELIGIBLE** (Table 4(A)(5)).
- If the vehicle context is unconfirmed: ITC is routed to **`REVIEW_REQUIRED_FACTS_PENDING`**, prompting the Tax Professional to confirm vehicle seating or commercial purpose before filing.

---

## 4. GSTR-3B TABLE 4 ARITHMETICAL RECONCILIATION

| Line Item | Description | Model A (Direct Net Filing) | Model B (Circular 170/2022) |
| :--- | :--- | :--- | :--- |
| **Table 4(A)(1)** | Import of Goods | ₹0.00 | ₹0.00 |
| **Table 4(A)(3)** | Inward supplies liable to RCM | ₹15,000.00 | ₹15,000.00 |
| **Table 4(A)(5)** | All other ITC (Gross) | ₹3,20,000.00 | ₹3,38,000.00 |
| **Table 4(B)(1)** | As per Rules 38, 42 & 43 and Sec 17(5) | ₹0.00 | ₹18,000.00 (Permanent) |
| **Table 4(B)(2)** | Others (Rule 37 / Rule 37A Temporary) | ₹21,250.00 | ₹21,250.00 |
| **Table 4(C)** | **Net ITC Available (4(A) - 4(B))** | **₹3,13,750.00** | **₹3,13,750.00** |
| **Table 4(D)(1)** | ITC reclaimed (included in 4(A)) | ₹0.00 | ₹0.00 |
| **Table 4(D)(2)** | Ineligible ITC under Sec 16(4) & Pos | ₹0.00 | ₹0.00 |

Both models reconcile to **₹3,13,750.00 Net ITC**. The previous ₹286,750 figure was identified as a double-deduction defect in an earlier revision and was permanently eliminated.

---

## 5. AUDIT CONCLUSION
The inward accounting engine, ITC ledger, and GSTR-3B Table 4 compilation logic are fully compliant with the CGST Act, CGST Rules, and Circular 170/02/2022-GST.
