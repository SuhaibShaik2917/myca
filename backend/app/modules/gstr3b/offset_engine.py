from decimal import Decimal, ROUND_HALF_UP
from typing import List, Tuple
from app.modules.gstr3b.schemas import (
    TaxBreakdown, OffsetHeadDetail, OffsetResult
)

def quant(val: Decimal) -> Decimal:
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def compute_statutory_tax_offset(
    liability: TaxBreakdown,
    credit: TaxBreakdown,
    cash_ledger: TaxBreakdown = None
) -> OffsetResult:
    """
    Statutory Electronic Credit Ledger Offset Engine complying with:
    - Section 49(5) of the CGST Act, 2017
    - Section 49A & Section 49B (inserted via CGST Amendment Act, 2018)
    - Rule 88A (inserted via Notification No. 16/2019 - Central Tax, 29th March 2019)
    - Circular No. 98/17/2019-GST (clarification on utilization of ITC under Rule 88A)

    Statutory Utilization Sequence:
    ------------------------------
    1. IGST Credit:
       - FIRST: Must be utilized towards IGST liability until IGST liability is 0 or IGST credit is 0.
       - SECOND: Any remaining IGST credit MAY be utilized towards CGST and SGST liabilities in ANY proportion/order.
         (Optimized here proportionally to balance CGST and SGST remaining liabilities).
       - MANDATORY CONDITION: Input tax credit on account of CGST or SGST can ONLY be utilized AFTER the credit
         on account of IGST has been fully exhausted (Rule 88A Proviso).
    2. CGST Credit:
       - FIRST: Towards CGST liability.
       - SECOND: Towards IGST liability (only if IGST credit is 0 and IGST liability remains).
       - PROHIBITED: CGST credit CANNOT be utilized for payment of SGST (Section 49(5)(e)).
    3. SGST Credit:
       - FIRST: Towards SGST liability.
       - SECOND: Towards IGST liability (only if IGST credit is 0 and IGST liability remains).
       - PROHIBITED: SGST credit CANNOT be utilized for payment of CGST (Section 49(5)(f)).
    4. Cess:
       - Cess credit can ONLY offset Cess liability. Cannot cross-offset GST heads.
    5. Cash Payment Requirement:
       - Any liability remaining unpaid after credit utilization must be discharged via Cash Ledger (PMT-06).
    """
    audit_trail: List[str] = []

    # Current Liabilities
    liab_igst = quant(liability.igst)
    liab_cgst = quant(liability.cgst)
    liab_sgst = quant(liability.sgst)
    liab_cess = quant(liability.cess)

    # Current Available Credits
    cred_igst = quant(credit.igst)
    cred_cgst = quant(credit.cgst)
    cred_sgst = quant(credit.sgst)
    cred_cess = quant(credit.cess)

    audit_trail.append(f"Initial Liabilities — IGST: ₹{liab_igst}, CGST: ₹{liab_cgst}, SGST: ₹{liab_sgst}, Cess: ₹{liab_cess}")
    audit_trail.append(f"Available Net ITC — IGST: ₹{cred_igst}, CGST: ₹{cred_cgst}, SGST: ₹{cred_sgst}, Cess: ₹{cred_cess}")

    # Head tracking
    h_igst = OffsetHeadDetail(liability_before=liab_igst)
    h_cgst = OffsetHeadDetail(liability_before=liab_cgst)
    h_sgst = OffsetHeadDetail(liability_before=liab_sgst)
    h_cess = OffsetHeadDetail(liability_before=liab_cess)

    # -------------------------------------------------------------------------
    # STEP 1: Rule 88A — IGST Credit towards IGST Liability
    # -------------------------------------------------------------------------
    use_igst_for_igst = min(cred_igst, liab_igst)
    h_igst.paid_by_igst_credit = quant(use_igst_for_igst)
    liab_igst -= use_igst_for_igst
    cred_igst -= use_igst_for_igst
    audit_trail.append(f"Step 1 (Rule 88A): Utilized ₹{use_igst_for_igst} IGST credit against IGST liability. Remaining IGST credit: ₹{cred_igst}, Remaining IGST liability: ₹{liab_igst}")

    # -------------------------------------------------------------------------
    # STEP 2: Rule 88A — Remaining IGST Credit towards CGST and SGST Liabilities
    # -------------------------------------------------------------------------
    if cred_igst > Decimal("0.00"):
        # Equal/proportional split between CGST and SGST to optimize working capital
        combined_cgst_sgst_liab = liab_cgst + liab_sgst
        if combined_cgst_sgst_liab > Decimal("0.00"):
            # If IGST credit covers both fully:
            if cred_igst >= combined_cgst_sgst_liab:
                h_cgst.paid_by_igst_credit = quant(liab_cgst)
                h_sgst.paid_by_igst_credit = quant(liab_sgst)
                cred_igst -= combined_cgst_sgst_liab
                liab_cgst = Decimal("0.00")
                liab_sgst = Decimal("0.00")
                audit_trail.append(f"Step 2 (Rule 88A): Utilized IGST credit to fully extinguish both CGST and SGST liabilities. Remaining IGST credit: ₹{cred_igst}")
            else:
                # Proportional allocation
                half_share = quant(cred_igst / Decimal("2.00"))
                # Allocate to CGST
                alloc_cgst = min(half_share, liab_cgst)
                h_cgst.paid_by_igst_credit = alloc_cgst
                liab_cgst -= alloc_cgst
                cred_igst -= alloc_cgst

                # Remainder of IGST credit to SGST
                alloc_sgst = min(cred_igst, liab_sgst)
                h_sgst.paid_by_igst_credit = alloc_sgst
                liab_sgst -= alloc_sgst
                cred_igst -= alloc_sgst

                # If any IGST credit still remains and CGST still has liability, apply it
                if cred_igst > Decimal("0.00") and liab_cgst > Decimal("0.00"):
                    extra_cgst = min(cred_igst, liab_cgst)
                    h_cgst.paid_by_igst_credit += extra_cgst
                    liab_cgst -= extra_cgst
                    cred_igst -= extra_cgst

                audit_trail.append(f"Step 2 (Rule 88A): Allocated IGST credit — ₹{h_cgst.paid_by_igst_credit} to CGST, ₹{h_sgst.paid_by_igst_credit} to SGST. Remaining IGST credit: ₹{cred_igst}")

    # -------------------------------------------------------------------------
    # MANDATORY GATE: Check that IGST credit is fully exhausted before CGST/SGST utilization
    # -------------------------------------------------------------------------
    if cred_igst > Decimal("0.00"):
        audit_trail.append(f"Note: Unutilized IGST credit of ₹{cred_igst} carried forward. CGST & SGST credits cannot be used as liabilities are already zero.")
    else:
        audit_trail.append("Rule 88A Proviso Gate Satisfied: IGST credit fully exhausted (₹0.00). CGST & SGST credit utilization unlocked.")

        # ---------------------------------------------------------------------
        # STEP 3: CGST Credit Utilization (Section 49(5)(a) & (b))
        # ---------------------------------------------------------------------
        # 3a. CGST credit towards CGST liability
        use_cgst_for_cgst = min(cred_cgst, liab_cgst)
        h_cgst.paid_by_cgst_credit = quant(use_cgst_for_cgst)
        liab_cgst -= use_cgst_for_cgst
        cred_cgst -= use_cgst_for_cgst
        audit_trail.append(f"Step 3a: Utilized ₹{use_cgst_for_cgst} CGST credit against CGST liability. Remaining CGST credit: ₹{cred_cgst}, Remaining CGST liability: ₹{liab_cgst}")

        # 3b. Remaining CGST credit towards remaining IGST liability
        if cred_cgst > Decimal("0.00") and liab_igst > Decimal("0.00"):
            use_cgst_for_igst = min(cred_cgst, liab_igst)
            h_igst.paid_by_cgst_credit = quant(use_cgst_for_igst)
            liab_igst -= use_cgst_for_igst
            cred_cgst -= use_cgst_for_igst
            audit_trail.append(f"Step 3b: Utilized ₹{use_cgst_for_igst} CGST credit against IGST liability. Remaining CGST credit: ₹{cred_cgst}, Remaining IGST liability: ₹{liab_igst}")

        # ---------------------------------------------------------------------
        # STEP 4: SGST Credit Utilization (Section 49(5)(c) & (d))
        # ---------------------------------------------------------------------
        # 4a. SGST credit towards SGST liability
        use_sgst_for_sgst = min(cred_sgst, liab_sgst)
        h_sgst.paid_by_sgst_credit = quant(use_sgst_for_sgst)
        liab_sgst -= use_sgst_for_sgst
        cred_sgst -= use_sgst_for_sgst
        audit_trail.append(f"Step 4a: Utilized ₹{use_sgst_for_sgst} SGST credit against SGST liability. Remaining SGST credit: ₹{cred_sgst}, Remaining SGST liability: ₹{liab_sgst}")

        # 4b. Remaining SGST credit towards remaining IGST liability
        if cred_sgst > Decimal("0.00") and liab_igst > Decimal("0.00"):
            use_sgst_for_igst = min(cred_sgst, liab_igst)
            h_igst.paid_by_sgst_credit = quant(use_sgst_for_igst)
            liab_igst -= use_sgst_for_igst
            cred_sgst -= use_sgst_for_igst
            audit_trail.append(f"Step 4b: Utilized ₹{use_sgst_for_igst} SGST credit against IGST liability. Remaining SGST credit: ₹{cred_sgst}, Remaining IGST liability: ₹{liab_igst}")

    # -------------------------------------------------------------------------
    # STEP 5: Cess Credit Utilization
    # -------------------------------------------------------------------------
    use_cess_for_cess = min(cred_cess, liab_cess)
    h_cess.paid_by_cess_credit = quant(use_cess_for_cess)
    liab_cess -= use_cess_for_cess
    cred_cess -= use_cess_for_cess

    # Calculate total credit used per head
    h_igst.total_credit_used = quant(h_igst.paid_by_igst_credit + h_igst.paid_by_cgst_credit + h_igst.paid_by_sgst_credit)
    h_cgst.total_credit_used = quant(h_cgst.paid_by_igst_credit + h_cgst.paid_by_cgst_credit)
    h_sgst.total_credit_used = quant(h_sgst.paid_by_igst_credit + h_sgst.paid_by_sgst_credit)
    h_cess.total_credit_used = quant(h_cess.paid_by_cess_credit)

    # Remaining liability per head
    h_igst.remaining_liability = quant(liab_igst)
    h_cgst.remaining_liability = quant(liab_cgst)
    h_sgst.remaining_liability = quant(liab_sgst)
    h_cess.remaining_liability = quant(liab_cess)

    # -------------------------------------------------------------------------
    # STEP 6: Cash Ledger Discharge & PMT-06 Challan Calculation
    # -------------------------------------------------------------------------
    # Check if cash ledger balances were provided:
    cash_igst = quant(cash_ledger.igst) if cash_ledger else Decimal("0.00")
    cash_cgst = quant(cash_ledger.cgst) if cash_ledger else Decimal("0.00")
    cash_sgst = quant(cash_ledger.sgst) if cash_ledger else Decimal("0.00")
    cash_cess = quant(cash_ledger.cess) if cash_ledger else Decimal("0.00")

    # Apply cash ledger balances
    paid_cash_igst = min(cash_igst, h_igst.remaining_liability)
    h_igst.paid_by_cash = quant(paid_cash_igst)
    h_igst.challan_required = quant(h_igst.remaining_liability - paid_cash_igst)

    paid_cash_cgst = min(cash_cgst, h_cgst.remaining_liability)
    h_cgst.paid_by_cash = quant(paid_cash_cgst)
    h_cgst.challan_required = quant(h_cgst.remaining_liability - paid_cash_cgst)

    paid_cash_sgst = min(cash_sgst, h_sgst.remaining_liability)
    h_sgst.paid_by_cash = quant(paid_cash_sgst)
    h_sgst.challan_required = quant(h_sgst.remaining_liability - paid_cash_sgst)

    paid_cash_cess = min(cash_cess, h_cess.remaining_liability)
    h_cess.paid_by_cash = quant(paid_cash_cess)
    h_cess.challan_required = quant(h_cess.remaining_liability - paid_cash_cess)

    total_credit_paid = quant(h_igst.total_credit_used + h_cgst.total_credit_used + h_sgst.total_credit_used + h_cess.total_credit_used)
    total_cash_paid = quant(h_igst.paid_by_cash + h_cgst.paid_by_cash + h_sgst.paid_by_cash + h_cess.paid_by_cash)
    total_challan_req = quant(h_igst.challan_required + h_cgst.challan_required + h_sgst.challan_required + h_cess.challan_required)

    audit_trail.append(f"Offset Summary — Total Paid by Credit: ₹{total_credit_paid}, Total Paid from Existing Cash Ledger: ₹{total_cash_paid}, Total Cash Challan (PMT-06) Required: ₹{total_challan_req}")

    remaining_credit = TaxBreakdown(
        igst=cred_igst,
        cgst=cred_cgst,
        sgst=cred_sgst,
        cess=cred_cess,
        total=quant(cred_igst + cred_cgst + cred_sgst + cred_cess)
    )

    return OffsetResult(
        igst=h_igst,
        cgst=h_cgst,
        sgst=h_sgst,
        cess=h_cess,
        total_tax_paid_by_credit=total_credit_paid,
        total_tax_paid_by_cash=total_cash_paid,
        total_challan_required=total_challan_req,
        remaining_credit_balance=remaining_credit,
        statutory_ordering_audit=audit_trail
    )
