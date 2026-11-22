# MyCA — FINAL GST STATUTORY COMPLIANCE & ACCOUNTING INTEGRITY AUDIT

**Audit Date:** November 23, 2026  
**Auditor:** Principal GST Regulatory & Statutory Audit Specialist  
**Statutory Basis:** Central Goods and Services Tax (CGST) Act, 2017 & CGST Rules, 2017  
**Core Milestones Audited:** Milestone 2 (Inward Statutory Engine) & Milestone 3 (GSTR-3B Offset & Filing)

---

## 1. STATUTORY RESOLUTION MATRIX FOR AUDIT FINDINGS

| Finding Ref | Statutory Issue Identified | MyCA Codebase Resolution & Provenance | Verification Test |
| :--- | :--- | :--- | :--- |
| **F-01** | Conflation of Rule 37 (180-day unpaid) and Rule 37A (supplier non-filing) | **Strictly Separated:** `itc_engine.py` implements dedicated logic branches for Rule 37 vs Rule 37A with distinct deadlines, interest liabilities, and re-claim mechanics. | `test_milestone2_7_2_13_period_chronology_and_reversal_lineage.py` |
| **F-02** | October vs November 2B Chronology & Cut-off Dates | Formalized filing periods: GSTR-2B generation cut-off (14th of following month) strictly respects invoice issue dates and portal auto-population dates. | `test_future_dated_ledger_events_strictly_quarantined_from_verified_snapshot` |
| **F-03** | Future-dated supplier fixture quarantine | Isolated synthetic mock fixtures with future dates (`2026-02-15`, `2026-11-30`) into dedicated test sandboxes; production ledger rejects future dates. | `test_future_dated_ledger_events_strictly_quarantined_from_verified_snapshot` |
| **F-04** | Section 17(5)(a)/(ab) vehicle seating threshold reconciliation | **Reconciled to Statutory Text:** Approved capacity of **not more than 13 persons** ($\le 13$) is BLOCKED; capacity of **more than 13 persons** ($> 13$, i.e. 14+) is ELIGIBLE (outside Section 17(5)(a) restriction, subject to Section 16 general conditions). Boundary tests prove 13 seats blocked and 14 seats eligible. | `test_statutory_section_17_5_boundaries_and_reference_grants.py::test_section_17_5_exact_13_seats_vs_14_seats_boundary` |
| **F-05** | Dual Evidence Standard for Statutory Exceptions | Commercial permit ALONE does not grant Section 17(5)(a)(B) exception. Requires **BOTH** commercial passenger permit AND actual use for making taxable passenger transport supplies. Missing permit or unverified taxable use routes to `REVIEW_REQUIRED_FACTS_PENDING`. | `test_statutory_section_17_5_boundaries_and_reference_grants.py::test_section_17_5_goods_transport_and_statutory_facts` |
| **F-06** | Section 17(5)(ab) Proviso (ii) Specific Provisions | Explicit exceptions evaluated for **motor vehicle manufacturers** and **general insurers** receiving repair, servicing, and insurance services. | `test_statutory_section_17_5_boundaries_and_reference_grants.py::test_section_17_5_ab_vehicle_repair_lineage` |
| **F-07** | Incomplete Factual Records & Missing Facts Handling | Missing seating capacity on otherwise verified RC routes to `REVIEW_REQUIRED_FACTS_PENDING` (not confirmed block). Invoices with missing or empty facts dictionaries route to `REVIEW_REQUIRED_FACTS_PENDING`. | `test_statutory_section_17_5_boundaries_and_reference_grants.py::test_section_17_5_goods_transport_and_statutory_facts` |
| **F-08** | Model A vs Model B Net ITC reconciliation | Both Model A (direct net) and Model B (Circular 170 Gross auto-population) reconcile to exact **₹3,13,750.00**. | `test_unified_financial_report_reconciliation` |

---

## 2. DEEP STATUTORY ANALYSIS: SECTION 17(5)(a) & SECTION 17(5)(ab)

### Statutory Text & Legal Standard
Section 17(5)(a) of the CGST Act, 2017 blocks input tax credit on:
> *"motor vehicles for transportation of persons having approved seating capacity of **not more than thirteen persons (including the driver)**, except when they are used for making the following taxable supplies, namely:—*  
> *(A) further supply of such motor vehicles; or*  
> *(B) transportation of passengers; or*  
> *(C) imparting training on driving such motor vehicles;"*

Section 17(5)(ab) blocks:
> *"services of general insurance, servicing, repair and maintenance in so far as they relate to motor vehicles, vessels or aircraft referred to in clause (a) or clause (aa):*  
> *Provided that the input tax credit in respect of such services shall be available—*  
> *(i) where the motor vehicles, vessels or aircraft referred to in clause (a) or clause (aa) are used for the purposes specified therein;*  
> *(ii) where received by a taxable person engaging in the manufacture of such motor vehicles, vessels or aircraft; or in the supply of general insurance services in respect of such motor vehicles, vessels or aircraft insured by him;"*

### Concrete Implementation & Boundary Enforcement (`itc_engine.py`)

1. **Exact 13-Seat Boundary Condition:**
   - A vehicle with an approved seating capacity of **13 persons (including the driver)** falls within the statutory phrase *"not more than thirteen persons"*.
   - **Determination:** ITC is **BLOCKED** (`BLOCKED_CREDIT_CONFIRMED`) under Section 17(5)(a), and repair services are BLOCKED under Section 17(5)(ab), unless an affirmative statutory exception applies.

2. **Exact 14-Seat (and Greater) Boundary Condition:**
   - A vehicle with an approved seating capacity of **14 persons (including the driver)** (e.g., a 14-seater minibus, 25-seater staff bus, or 50-seater coach) exceeds 13 persons and is **outside the scope of Section 17(5)(a)**.
   - **Determination:** ITC on acquisition is **ELIGIBLE** (`ELIGIBLE_STATUTORY_EXCEPTION`) (subject to Section 16 general conditions), and repair services under SAC 998729 are **ELIGIBLE**.

3. **Goods Transport Vehicles (HSN 8704 / Trucks / Tippers):**
   - Section 17(5)(a) applies strictly to *"motor vehicles for transportation of persons"*.
   - Goods transport vehicles are outside the clause (a) block entirely.
   - **Determination:** ITC is **ELIGIBLE** (`ELIGIBLE_STATUTORY_EXCEPTION`) when vehicle registration / RC confirms goods carriage category.

4. **Dual-Evidence Requirement for Statutory Exceptions:**
   - **Passenger Transportation (Section 17(5)(a)(B)):**
     - A commercial passenger permit alone does NOT grant eligibility. The taxpayer must also provide evidence that the vehicle is **actually used for making taxable passenger transport supplies**.
     - If only the permit is present or only passenger transport use is claimed, the assessment routes to **`REVIEW_REQUIRED_FACTS_PENDING`**.
     - Only when **BOTH** commercial permit AND actual taxable passenger transport use are verified does it grant **`ELIGIBLE_STATUTORY_EXCEPTION`**.
   - **Further Supply (Section 17(5)(a)(A)):** Requires both dealership authorization/license AND actual outward taxable vehicle supplies.
   - **Driving School Training (Section 17(5)(a)(C)):** Requires both motor driving school license AND actual training vehicle use.

5. **Section 17(5)(ab) Proviso (ii) Specific Provisions:**
   - **Motor Vehicle Manufacturers:** ITC on general insurance, servicing, and repair received by vehicle manufacturers is **ELIGIBLE** (`ELIGIBLE_STATUTORY_EXCEPTION`).
   - **General Insurers:** ITC on repair and maintenance services in respect of vehicles insured by the taxpayer is **ELIGIBLE** (`ELIGIBLE_STATUTORY_EXCEPTION`).

6. **Missing Facts & Incomplete Factual Records:**
   - When RC context is verified but approved seating capacity is omitted (`seating_capacity: None`), the engine does NOT default to a confirmed block; it routes to **`REVIEW_REQUIRED_FACTS_PENDING`** pending RC seating verification.
   - Invoices with empty or missing facts dictionaries route to **`REVIEW_REQUIRED_FACTS_PENDING`**.

---

## 3. RULE 37 VS RULE 37A SEPARATION SUMMARY

| Statutory Feature | CGST Rule 37 | CGST Rule 37A |
| :--- | :--- | :--- |
| **Statutory Trigger** | Failure to pay supplier consideration + tax within 180 days of invoice. | Supplier failed to furnish GSTR-3B by 30th Sept following financial year. |
| **Filing Form & Table** | GSTR-3B Table 4(B)(2) | GSTR-3B Table 4(B)(2) |
| **Reversal Tax Period** | Tax period immediately following 180-day expiry. | On or before 30th November following financial year. |
| **Interest Liability** | Mandatory 18% p.a. under Section 50(1) from date of availment. | **Zero interest** if reversed on or before 30th Nov; 18% p.a. if reversed after 30th Nov. |
| **Re-Availment Right** | Re-claimable upon subsequent payment without Section 16(4) time bar. | Re-claimable in Table 4(A)(5) and Table 4(D)(1) when supplier files GSTR-3B. |

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

Both models reconcile to **₹3,13,750.00 Net ITC**. The statutory engine adheres strictly to the CGST Act, CGST Rules, and Circular 170/02/2022-GST.
