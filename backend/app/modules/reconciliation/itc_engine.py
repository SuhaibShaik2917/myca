import logging
from decimal import Decimal
from typing import List, Dict, Any, Optional
from datetime import date, datetime
from .schemas import ITCEligibilityEnum, ITCAssessmentOut, Rule37PaymentCheck, Rule37AFilingCheck

logger = logging.getLogger("reconciliation.itc_engine")


def calculate_rule_37_interest(
    reversal_tax: Decimal,
    ecl_minimum_balance: Optional[Decimal] = None,
    utilisation_date: Optional[date] = None,
    reversal_date: Optional[date] = None,
    interest_rate_pct: Decimal = Decimal("18.00"),
    tax_head: str = "TOTAL",
    ecl_head_balances: Optional[Dict[str, Decimal]] = None,
    ecl_transactions: Optional[List[Dict[str, Any]]] = None,
    daily_ledger_chronology: Optional[List[Dict[str, Any]]] = None,
    invoice_date: Optional[date] = None,
    availment_date: Optional[date] = None
) -> Dict[str, Any]:
    """
    Computes statutory interest liability under Section 50(3) read with Rule 88B(3) of CGST Rules
    and CBIC Circular No. 192/04/2023-GST, with statutory separation between Rule 37(1) liability
    and Rule 88B(3) utilization-dependent interest:

    1. Interest is payable ONLY if and to the extent that the wrongly availed credit was actually utilised.
    2. Under Rule 88B(3), credit is treated as utilised only when the balance in the Electronic Credit
       Ledger falls below the amount of wrongly availed credit.
    3. Tax-Head Aggregation Rules (Circular 192/04/2023-GST & Section 49(5)):
       - IGST Wrong Availment: Total balance across IGST + CGST + SGST heads taken together is evaluated
         because IGST credit can be cross-utilized towards IGST, CGST, and SGST liabilities.
         *Compensation Cess is strictly excluded* from this combined balance.
       - CGST Wrong Availment: Total balance across CGST + IGST heads is evaluated under Section 49(5)(b).
         (SGST and Cess are excluded).
       - SGST Wrong Availment: Total balance across SGST + IGST heads is evaluated under Section 49(5)(c).
         (CGST and Cess are excluded).
       - Cess Wrong Availment: Only Compensation Cess ledger balance is evaluated (cross-utilization prohibited).
    4. If relevant minimum Electronic Credit Ledger balance >= reversal_tax: NEVER utilised -> Utilised Amount = 0,
       and Interest under Section 50(3) is Nil (₹0.00).
    5. If relevant minimum Electronic Credit Ledger balance < reversal_tax, Utilised Amount = reversal_tax - max(0, relevant_ecl).
       Interest runs at 18% p.a. on the utilised shortfall from the date of utilisation (debit) to the
       date of reversal in Form GSTR-3B.
    6. If daily_ledger_chronology is provided, interest is calculated day-by-day or event-by-event on
       the exact shortfall on each day, capturing dips, multi-dip episodes, recovery periods, and partial reversals.
    7. Statutory Dual Assessment: Computes both the legally prevailing Section 50(3) utilization-dependent
       determination and the literal Section 50(1) time-based assessment (from 181st day and from availment date)
       to provide complete audit defense and management risk visibility.
    """
    from datetime import timedelta

    rev_tax = Decimal(str(reversal_tax or 0)).quantize(Decimal("0.01"))
    rate = Decimal(str(interest_rate_pct or "18.00"))
    rev_dt = reversal_date or date.today()
    t_head = (tax_head or "TOTAL").upper()

    computed_u_date = utilisation_date
    relevant_ecl: Optional[Decimal] = None
    cess_excluded = True

    # Determine statutory basis string
    if t_head == "IGST":
        statutory_basis = "Section 50(3) read with Rule 88B(3) and CBIC Circular No. 192/04/2023-GST"
    else:
        statutory_basis = "Section 50(3) read with Rule 88B(3)"

    # Compute dual statutory assessment if dates are provided
    dual_assessment: Optional[Dict[str, Any]] = None
    if invoice_date or availment_date:
        inv_dt = invoice_date or (availment_date if availment_date else rev_dt)
        avail_dt = availment_date or inv_dt
        day_180 = inv_dt + timedelta(days=180)
        day_181 = inv_dt + timedelta(days=181)

        days_beyond_180 = max(0, (rev_dt - day_181).days + 1) if rev_dt >= day_181 else 0
        int_sec_50_1_post_180 = (rev_tax * (rate / Decimal("100.00")) * Decimal(days_beyond_180) / Decimal("365")).quantize(Decimal("0.01"))

        days_from_availment = max(0, (rev_dt - avail_dt).days) if rev_dt >= avail_dt else 0
        int_sec_50_1_from_avail = (rev_tax * (rate / Decimal("100.00")) * Decimal(days_from_availment) / Decimal("365")).quantize(Decimal("0.01"))

        dual_assessment = {
            "certification_status": "REQUIRES_TAX_PROFESSIONAL_REVIEW",
            "prevent_automatic_certification": True,
            "unresolved_legal_interpretation": (
                "Whether the phrase 'along with interest payable thereon under section 50' in Rule 37(1) "
                "invokes Section 50(1) (time-based tax default) or Section 50(3) (utilization-based credit default under Rule 88B(3)) "
                "remains an open legal controversy. Automatic statutory certification of Nil interest is withheld pending manual tax professional review."
            ),
            "legal_assumptions": {
                "taxpayer_defense_sec_50_3": (
                    "Taxpayer relies on Section 50(3) read with Rule 88B(3) (retrospective from 01.07.2017 per Finance Act 2022) "
                    "and Circular 192/04/2023-GST: since minimum ECL balance exceeded reversal tax throughout the default period, "
                    "credit was never utilized and interest liability is Nil (₹0.00)."
                ),
                "departmental_audit_view_sec_50_1": (
                    "Tax authorities may assess interest under Section 50(1) on a strict calendar-day basis from the 181st day "
                    "or from initial availment date, contending that Rule 37 creates an independent tax liability without ECL balance defense."
                )
            },
            "statutory_provisions": {
                "rule_37_1": "Rule 37(1) CGST Rules: Reversal mandatory in GSTR-3B immediately following 180 days along with interest under Section 50.",
                "section_50_3_rule_88b_3": "Prevailing legal view post-Finance Act 2022: Interest applies strictly on wrongly availed and UTILIZED credit. Unutilized ECL balance attracts Nil interest.",
                "section_50_1_departmental_view": "Departmental alternative: Section 50(1) interest on time elapsed regardless of ECL balance."
            },
            "dates": {
                "invoice_date": str(inv_dt),
                "availment_date": str(avail_dt),
                "day_180": str(day_180),
                "day_181": str(day_181),
                "reversal_date": str(rev_dt),
                "days_beyond_180": days_beyond_180,
                "days_from_availment": days_from_availment
            },
            "computations": {
                "basis_a_rule_88b_3_utilized": {
                    "statutory_rule": "Section 50(3) read with Rule 88B(3)",
                    "interest_rate": f"{rate}% p.a.",
                    "interest_amount": Decimal("0.00"),  # updated below based on actual utilization
                    "legal_reasoning": "Credit remained unutilized in Electronic Credit Ledger; no financial loss to Government revenue."
                },
                "basis_b1_sec_50_1_from_181st_day": {
                    "statutory_rule": "Section 50(1) from 181st day",
                    "days": days_beyond_180,
                    "interest_rate": f"{rate}% p.a.",
                    "interest_amount": int_sec_50_1_post_180,
                    "legal_reasoning": "Interest computed strictly for days of default beyond the 180-day statutory credit window."
                },
                "basis_b2_sec_50_1_from_availment": {
                    "statutory_rule": "Section 50(1) from initial availment date",
                    "days": days_from_availment,
                    "interest_rate": f"{rate}% p.a.",
                    "interest_amount": int_sec_50_1_from_avail,
                    "legal_reasoning": "Conservative departmental assessment running interest from the original return filing date of availment."
                }
            }
        }

    # 1. Evaluate from daily ledger chronology if provided (Comprehensive Rule 88B(3) day-by-day sequence)
    if daily_ledger_chronology:
        chronology_audit_log = []
        total_interest = Decimal("0.00")
        current_disputed_itc = rev_tax
        total_days_utilised = 0
        max_shortfall = Decimal("0.00")
        first_util_date: Optional[date] = None
        in_dip = False
        dip_episodes = 0
        recovery_episodes = 0
        min_chronology_balance: Optional[Decimal] = None

        for entry in daily_ledger_chronology:
            e_dt_raw = entry.get("date") or entry.get("transaction_date")
            if isinstance(e_dt_raw, str):
                e_dt = datetime.strptime(e_dt_raw[:10], "%Y-%m-%d").date()
            elif isinstance(e_dt_raw, date):
                e_dt = e_dt_raw
            else:
                e_dt = None

            # Handle partial reversal on this date
            partial_rev = Decimal(str(entry.get("partial_reversal") or 0))
            if partial_rev > Decimal("0.00"):
                current_disputed_itc = max(Decimal("0.00"), current_disputed_itc - partial_rev)

            # Determine relevant closing balance for the tax head
            if "closing_balance" in entry:
                rel_bal = Decimal(str(entry["closing_balance"]))
            else:
                b_i = Decimal(str(entry.get("balance_igst") or 0))
                b_c = Decimal(str(entry.get("balance_cgst") or 0))
                b_s = Decimal(str(entry.get("balance_sgst") or 0))
                b_cs = Decimal(str(entry.get("balance_cess") or 0))
                if t_head == "IGST":
                    rel_bal = b_i + b_c + b_s  # Circular 192: Cess excluded
                elif t_head == "CGST":
                    rel_bal = b_c + b_i
                elif t_head == "SGST":
                    rel_bal = b_s + b_i
                elif t_head == "CESS":
                    rel_bal = b_cs
                else:
                    rel_bal = b_i + b_c + b_s

            if min_chronology_balance is None or rel_bal < min_chronology_balance:
                min_chronology_balance = rel_bal

            days = int(entry.get("days", 1))

            # Statutory Rule 88B(3) utilization test:
            # Shortfall occurs when balance in Electronic Credit Ledger falls below the disputed wrongly availed amount
            shortfall = max(Decimal("0.00"), current_disputed_itc - rel_bal)

            if shortfall > Decimal("0.00"):
                if not in_dip:
                    in_dip = True
                    dip_episodes += 1
                if first_util_date is None and e_dt is not None:
                    first_util_date = e_dt
                if shortfall > max_shortfall:
                    max_shortfall = shortfall
                total_days_utilised += days

                # Daily interest: shortfall * rate / 100 * days / 365
                day_interest = (shortfall * (rate / Decimal("100.00")) * Decimal(days) / Decimal("365")).quantize(Decimal("0.01"))
                total_interest += day_interest
            else:
                if in_dip:
                    in_dip = False
                    recovery_episodes += 1
                day_interest = Decimal("0.00")

            chronology_audit_log.append({
                "date": str(e_dt) if e_dt else None,
                "description": entry.get("description", "Daily Electronic Credit Ledger State"),
                "closing_balance": rel_bal,
                "disputed_itc_balance": current_disputed_itc,
                "utilized_shortfall": shortfall,
                "days": days,
                "interest_accrued": day_interest,
                "cumulative_interest": total_interest
            })

        min_bal = min_chronology_balance if min_chronology_balance is not None else Decimal("0.00")
        is_ut = total_interest > Decimal("0.00")

        if dual_assessment:
            dual_assessment["computations"]["basis_a_rule_88b_3_utilized"]["interest_amount"] = total_interest
            dual_assessment["computations"]["basis_a_rule_88b_3_utilized"]["days_utilized"] = total_days_utilised
            dual_assessment["computations"]["basis_a_rule_88b_3_utilized"]["utilized_shortfall"] = max_shortfall

        return {
            "reversal_tax": rev_tax,
            "ecl_minimum_balance": min_bal,
            "relevant_ecl_balance": min_bal,
            "tax_head": t_head,
            "cess_excluded": cess_excluded,
            "utilised_amount": max_shortfall,
            "utilisation_date": first_util_date,
            "reversal_date": rev_dt,
            "days_utilised": total_days_utilised,
            "interest_rate_pct": rate,
            "interest_amount": total_interest,
            "is_utilised": is_ut,
            "statutory_basis": statutory_basis + " (Transaction-Level Electronic Credit Ledger Chronology)",
            "explanation": (
                f"Evaluated {len(daily_ledger_chronology)} ledger event(s) under Rule 88B(3). "
                f"Identified {dip_episodes} utilization episode(s) and {recovery_episodes} recovery episode(s). "
                f"Total days utilized: {total_days_utilised} days. Total interest accrued: ₹{total_interest:,.2f}."
            ),
            "source_ecl_transactions": ecl_transactions,
            "daily_ledger_chronology": chronology_audit_log,
            "utilization_episodes": dip_episodes,
            "recovery_episodes": recovery_episodes,
            "dual_assessment": dual_assessment
        }

    # 2. Evaluate from chronological ECL transaction ledger if provided (legacy interval walk)
    if ecl_transactions:
        min_txn_balance: Optional[Decimal] = None
        first_dip_date: Optional[date] = None

        for txn in ecl_transactions:
            t_dt_raw = txn.get("date") or txn.get("transaction_date")
            if isinstance(t_dt_raw, str):
                t_dt = datetime.strptime(t_dt_raw[:10], "%Y-%m-%d").date()
            elif isinstance(t_dt_raw, date):
                t_dt = t_dt_raw
            else:
                t_dt = None

            b_igst = Decimal(str(txn.get("balance_igst") or 0))
            b_cgst = Decimal(str(txn.get("balance_cgst") or 0))
            b_sgst = Decimal(str(txn.get("balance_sgst") or 0))
            b_cess = Decimal(str(txn.get("balance_cess") or 0))

            if t_head == "IGST":
                txn_rel = b_igst + b_cgst + b_sgst  # Circular 192: Cess strictly excluded
            elif t_head == "CGST":
                txn_rel = b_cgst + b_igst
            elif t_head == "SGST":
                txn_rel = b_sgst + b_igst
            elif t_head == "CESS":
                txn_rel = b_cess
            else:
                txn_rel = b_igst + b_cgst + b_sgst

            if min_txn_balance is None or txn_rel < min_txn_balance:
                min_txn_balance = txn_rel

            if txn_rel < rev_tax and first_dip_date is None:
                first_dip_date = t_dt

        relevant_ecl = min_txn_balance
        if computed_u_date is None and first_dip_date is not None:
            computed_u_date = first_dip_date

    # 3. Evaluate from head balances snapshot if transactions not provided
    elif ecl_head_balances:
        b_igst = Decimal(str(ecl_head_balances.get("igst") or ecl_head_balances.get("IGST") or 0))
        b_cgst = Decimal(str(ecl_head_balances.get("cgst") or ecl_head_balances.get("CGST") or 0))
        b_sgst = Decimal(str(ecl_head_balances.get("sgst") or ecl_head_balances.get("SGST") or 0))
        b_cess = Decimal(str(ecl_head_balances.get("cess") or ecl_head_balances.get("CESS") or 0))

        if t_head == "IGST":
            relevant_ecl = b_igst + b_cgst + b_sgst
        elif t_head == "CGST":
            relevant_ecl = b_cgst + b_igst
        elif t_head == "SGST":
            relevant_ecl = b_sgst + b_igst
        elif t_head == "CESS":
            relevant_ecl = b_cess
        else:
            relevant_ecl = b_igst + b_cgst + b_sgst

    # 4. Direct minimum balance fallback
    elif ecl_minimum_balance is not None:
        relevant_ecl = Decimal(str(ecl_minimum_balance))

    # When relevant ECL balance >= reversal_tax: NEVER utilised -> Nil interest
    if relevant_ecl is not None and relevant_ecl >= rev_tax:
        if t_head == "IGST":
            reason = (
                f"Combined Electronic Credit Ledger minimum balance across IGST+CGST+SGST (₹{relevant_ecl:,.2f}, excluding Cess) "
                f">= wrongly availed IGST (₹{rev_tax:,.2f}). Per CBIC Circular 192/04/2023-GST, credit was "
                f"never utilised. Statutory interest under Section 50(3) is Nil (₹0.00)."
            )
        else:
            reason = (
                f"Electronic Credit Ledger relevant minimum balance (₹{relevant_ecl:,.2f}) >= reversal amount (₹{rev_tax:,.2f}). "
                f"Wrongly availed ITC was never utilised. Statutory interest under Section 50(3) is Nil (₹0.00)."
            )

        if dual_assessment:
            dual_assessment["computations"]["basis_a_rule_88b_3_utilized"]["interest_amount"] = Decimal("0.00")
            dual_assessment["computations"]["basis_a_rule_88b_3_utilized"]["days_utilized"] = 0
            dual_assessment["computations"]["basis_a_rule_88b_3_utilized"]["utilized_shortfall"] = Decimal("0.00")

        return {
            "reversal_tax": rev_tax,
            "ecl_minimum_balance": ecl_minimum_balance if ecl_minimum_balance is not None else relevant_ecl,
            "relevant_ecl_balance": relevant_ecl,
            "tax_head": t_head,
            "cess_excluded": cess_excluded,
            "utilised_amount": Decimal("0.00"),
            "utilized_amount": Decimal("0.00"),
            "utilisation_date": None,
            "reversal_date": rev_dt,
            "days_utilised": 0,
            "interest_rate_pct": rate,
            "interest_amount": Decimal("0.00"),
            "is_utilised": False,
            "statutory_basis": statutory_basis,
            "explanation": reason,
            "source_ecl_transactions": ecl_transactions,
            "dual_assessment": dual_assessment
        }

    # When utilised or ECL balance fell below reversal amount
    min_bal = max(Decimal("0.00"), relevant_ecl) if relevant_ecl is not None else Decimal("0.00")
    utilised = (rev_tax - min_bal).quantize(Decimal("0.01"))

    if computed_u_date and rev_dt > computed_u_date:
        days = (rev_dt - computed_u_date).days
    else:
        days = 0

    if days > 0 and utilised > Decimal("0.00"):
        interest = (utilised * (rate / Decimal("100.00")) * Decimal(days) / Decimal("365")).quantize(Decimal("0.01"))
    else:
        interest = Decimal("0.00")

    if t_head == "IGST":
        exp = (
            f"Combined Electronic Credit Ledger balance (IGST+CGST+SGST, excluding Cess) fell to ₹{min_bal:,.2f}, resulting in "
            f"statutory credit utilisation of ₹{utilised:,.2f} for {days} days ({computed_u_date} to {rev_dt}) "
            f"at {rate}% p.a. per Rule 88B(3) and CBIC Circular 192/04/2023-GST."
        )
    else:
        exp = (
            f"Relevant Electronic Credit Ledger balance fell to ₹{min_bal:,.2f}, resulting in actual credit utilisation of ₹{utilised:,.2f} "
            f"for {days} days ({computed_u_date} to {rev_dt}) at {rate}% p.a. under Section 50(3)."
        )

    if dual_assessment:
        dual_assessment["computations"]["basis_a_rule_88b_3_utilized"]["interest_amount"] = interest
        dual_assessment["computations"]["basis_a_rule_88b_3_utilized"]["days_utilized"] = days
        dual_assessment["computations"]["basis_a_rule_88b_3_utilized"]["utilized_shortfall"] = utilised

    return {
        "reversal_tax": rev_tax,
        "ecl_minimum_balance": ecl_minimum_balance if ecl_minimum_balance is not None else relevant_ecl,
        "relevant_ecl_balance": relevant_ecl,
        "tax_head": t_head,
        "cess_excluded": cess_excluded,
        "utilised_amount": utilised,
        "utilized_amount": utilised,
        "utilisation_date": computed_u_date,
        "reversal_date": rev_dt,
        "days_utilised": days,
        "interest_rate_pct": rate,
        "interest_amount": interest,
        "is_utilised": utilised > Decimal("0.00"),
        "statutory_basis": statutory_basis,
        "explanation": exp,
        "source_ecl_transactions": ecl_transactions,
        "dual_assessment": dual_assessment
    }

    # 2. Evaluate from chronological ECL transaction ledger if provided (legacy interval walk)
    if ecl_transactions:
        min_txn_balance: Optional[Decimal] = None
        first_dip_date: Optional[date] = None

        for txn in ecl_transactions:
            t_dt_raw = txn.get("date") or txn.get("transaction_date")
            if isinstance(t_dt_raw, str):
                t_dt = datetime.strptime(t_dt_raw[:10], "%Y-%m-%d").date()
            elif isinstance(t_dt_raw, date):
                t_dt = t_dt_raw
            else:
                t_dt = None

            b_igst = Decimal(str(txn.get("balance_igst") or 0))
            b_cgst = Decimal(str(txn.get("balance_cgst") or 0))
            b_sgst = Decimal(str(txn.get("balance_sgst") or 0))
            b_cess = Decimal(str(txn.get("balance_cess") or 0))

            if t_head == "IGST":
                txn_rel = b_igst + b_cgst + b_sgst  # Circular 192: Cess strictly excluded
            elif t_head == "CGST":
                txn_rel = b_cgst + b_igst
            elif t_head == "SGST":
                txn_rel = b_sgst + b_igst
            elif t_head == "CESS":
                txn_rel = b_cess
            else:
                txn_rel = b_igst + b_cgst + b_sgst

            if min_txn_balance is None or txn_rel < min_txn_balance:
                min_txn_balance = txn_rel

            if txn_rel < rev_tax and first_dip_date is None:
                first_dip_date = t_dt

        relevant_ecl = min_txn_balance
        if computed_u_date is None and first_dip_date is not None:
            computed_u_date = first_dip_date

    # 3. Evaluate from head balances snapshot if transactions not provided
    elif ecl_head_balances:
        b_igst = Decimal(str(ecl_head_balances.get("igst") or ecl_head_balances.get("IGST") or 0))
        b_cgst = Decimal(str(ecl_head_balances.get("cgst") or ecl_head_balances.get("CGST") or 0))
        b_sgst = Decimal(str(ecl_head_balances.get("sgst") or ecl_head_balances.get("SGST") or 0))
        b_cess = Decimal(str(ecl_head_balances.get("cess") or ecl_head_balances.get("CESS") or 0))

        if t_head == "IGST":
            relevant_ecl = b_igst + b_cgst + b_sgst
        elif t_head == "CGST":
            relevant_ecl = b_cgst + b_igst
        elif t_head == "SGST":
            relevant_ecl = b_sgst + b_igst
        elif t_head == "CESS":
            relevant_ecl = b_cess
        else:
            relevant_ecl = b_igst + b_cgst + b_sgst

    # 4. Direct minimum balance fallback
    elif ecl_minimum_balance is not None:
        relevant_ecl = Decimal(str(ecl_minimum_balance))
        relevant_ecl = Decimal(str(ecl_minimum_balance))

    # Determine statutory basis string
    if t_head == "IGST":
        statutory_basis = "Section 50(3) read with Rule 88B(3) and CBIC Circular No. 192/04/2023-GST"
    else:
        statutory_basis = "Section 50(3) read with Rule 88B(3)"

    # When relevant ECL balance >= reversal_tax: NEVER utilised -> Nil interest
    if relevant_ecl is not None and relevant_ecl >= rev_tax:
        if t_head == "IGST":
            reason = (
                f"Combined ECL minimum balance across IGST+CGST+SGST (₹{relevant_ecl:,.2f}, excluding Cess) "
                f">= wrongly availed IGST (₹{rev_tax:,.2f}). Per CBIC Circular 192/04/2023-GST, credit was "
                f"never utilised. Statutory interest under Section 50(3) is Nil (₹0.00)."
            )
        else:
            reason = (
                f"ECL relevant minimum balance (₹{relevant_ecl:,.2f}) >= reversal amount (₹{rev_tax:,.2f}). "
                f"Wrongly availed ITC was never utilised. Statutory interest under Section 50(3) is Nil (₹0.00)."
            )

        return {
            "reversal_tax": rev_tax,
            "ecl_minimum_balance": ecl_minimum_balance if ecl_minimum_balance is not None else relevant_ecl,
            "relevant_ecl_balance": relevant_ecl,
            "tax_head": t_head,
            "cess_excluded": cess_excluded,
            "utilised_amount": Decimal("0.00"),
            "utilized_amount": Decimal("0.00"),
            "utilisation_date": None,
            "reversal_date": rev_dt,
            "days_utilised": 0,
            "interest_rate_pct": rate,
            "interest_amount": Decimal("0.00"),
            "is_utilised": False,
            "statutory_basis": statutory_basis,
            "explanation": reason,
            "source_ecl_transactions": ecl_transactions,
            "dual_assessment": dual_assessment
        }

    # When utilised or ECL balance fell below reversal amount
    min_bal = max(Decimal("0.00"), relevant_ecl) if relevant_ecl is not None else Decimal("0.00")
    utilised = (rev_tax - min_bal).quantize(Decimal("0.01"))

    if computed_u_date and rev_dt > computed_u_date:
        days = (rev_dt - computed_u_date).days
    else:
        days = 0

    if days > 0 and utilised > Decimal("0.00"):
        interest = (utilised * (rate / Decimal("100.00")) * Decimal(days) / Decimal("365")).quantize(Decimal("0.01"))
    else:
        interest = Decimal("0.00")

    if t_head == "IGST":
        exp = (
            f"Combined ECL balance (IGST+CGST+SGST, excluding Cess) fell to ₹{min_bal:,.2f}, resulting in "
            f"statutory credit utilisation of ₹{utilised:,.2f} for {days} days ({computed_u_date} to {rev_dt}) "
            f"at {rate}% p.a. per Rule 88B(3) and CBIC Circular 192/04/2023-GST."
        )
    else:
        exp = (
            f"Relevant ECL balance fell to ₹{min_bal:,.2f}, resulting in actual credit utilisation of ₹{utilised:,.2f} "
            f"for {days} days ({computed_u_date} to {rev_dt}) at {rate}% p.a. under Section 50(3)."
        )

    if dual_assessment:
        dual_assessment["computations"]["basis_a_rule_88b_3_utilized"]["interest_amount"] = interest
        dual_assessment["computations"]["basis_a_rule_88b_3_utilized"]["days_utilized"] = days
        dual_assessment["computations"]["basis_a_rule_88b_3_utilized"]["utilized_shortfall"] = utilised

    return {
        "reversal_tax": rev_tax,
        "ecl_minimum_balance": ecl_minimum_balance if ecl_minimum_balance is not None else relevant_ecl,
        "relevant_ecl_balance": relevant_ecl,
        "tax_head": t_head,
        "cess_excluded": cess_excluded,
        "utilised_amount": utilised,
        "utilized_amount": utilised,
        "utilisation_date": computed_u_date,
        "reversal_date": rev_dt,
        "days_utilised": days,
        "interest_rate_pct": rate,
        "interest_amount": interest,
        "is_utilised": utilised > Decimal("0.00"),
        "statutory_basis": statutory_basis,
        "explanation": exp,
        "source_ecl_transactions": ecl_transactions,
        "dual_assessment": dual_assessment
    }


def simulate_sequential_ecl_cross_utilization(
    opening_balances: Optional[Dict[str, Any]] = None,
    output_liabilities: Optional[Dict[str, Any]] = None,
    igst_offset_order: Any = "CGST_THEN_SGST",
    itc_balances: Optional[Dict[str, Any]] = None,
    tax_liabilities: Optional[Dict[str, Any]] = None,
    igst_cross_utilization_order: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Simulates sequential tax liability offset and Electronic Credit Ledger (ECL) cross-utilization
    strictly adhering to Section 49, Section 49A, Section 49B of the CGST Act and Rule 88A:

    Statutory Hierarchy:
    1. IGST Credit Utilization:
       - Must be utilized FIRST towards payment of IGST output liability.
       - Any remaining IGST credit can be utilized towards payment of CGST and SGST liabilities
         in any order / proportion (e.g. CGST_THEN_SGST, SGST_THEN_CGST, EQUAL, or custom proportion).
       - Crucially: Entire IGST credit must be fully exhausted (to ₹0.00) before ANY CGST or SGST credit
         can be utilized (Rule 88A mandate).
       - When IGST credit is consumed by CGST, the available IGST pool is depleted sequentially, preventing
         the same IGST balance from being double-counted as simultaneously available against SGST (DEF-05).
    2. CGST Credit Utilization:
       - Utilized towards payment of remaining CGST output liability.
       - Any remaining CGST credit may be utilized towards IGST output liability ONLY if SGST credit
         is not utilized (and only if IGST liability remains).
       - CGST credit can NEVER be utilized towards SGST liability (Section 49(5)(e)).
    3. SGST Credit Utilization:
       - Utilized towards payment of remaining SGST output liability.
       - Any remaining SGST credit may be utilized towards IGST output liability (if IGST liability remains).
       - SGST credit can NEVER be utilized towards CGST liability (Section 49(5)(f)).
    4. Compensation Cess:
       - Can only be utilized towards payment of Cess liability. No cross-utilization permitted.
    """
    # Normalize inputs
    raw_balances = opening_balances or itc_balances or {}
    raw_liabilities = output_liabilities or tax_liabilities or {}
    offset_order = igst_cross_utilization_order or igst_offset_order or "CGST_THEN_SGST"

    def get_val(d: Dict[str, Any], key: str) -> Decimal:
        v = d.get(key.lower())
        if v is None:
            v = d.get(key.upper())
        return Decimal(str(v or 0))

    b_igst = get_val(raw_balances, "igst")
    b_cgst = get_val(raw_balances, "cgst")
    b_sgst = get_val(raw_balances, "sgst")
    b_cess = get_val(raw_balances, "cess")

    l_igst = get_val(raw_liabilities, "igst")
    l_cgst = get_val(raw_liabilities, "cgst")
    l_sgst = get_val(raw_liabilities, "sgst")
    l_cess = get_val(raw_liabilities, "cess")

    rem_igst_bal = b_igst
    rem_cgst_bal = b_cgst
    rem_sgst_bal = b_sgst
    rem_cess_bal = b_cess

    rem_igst_liab = l_igst
    rem_cgst_liab = l_cgst
    rem_sgst_liab = l_sgst
    rem_cess_liab = l_cess

    debit_history: List[Dict[str, Any]] = []

    # Step 1: Offset IGST liability with IGST credit
    igst_used_for_igst = min(rem_igst_bal, rem_igst_liab)
    rem_igst_bal -= igst_used_for_igst
    rem_igst_liab -= igst_used_for_igst
    if igst_used_for_igst > Decimal("0.00"):
        debit_history.append({
            "step": 1,
            "action": "IGST_CREDIT_FOR_IGST_LIABILITY",
            "debit_amount": igst_used_for_igst,
            "credit_head_used": "IGST",
            "liability_head_offset": "IGST",
            "remaining_igst_credit": rem_igst_bal,
            "remaining_igst_liability": rem_igst_liab
        })

    # Step 2: Offset CGST and SGST liabilities with remaining IGST credit (Rule 88A)
    # The pool must deplete sequentially, preventing double-counting!
    igst_used_for_cgst = Decimal("0.00")
    igst_used_for_sgst = Decimal("0.00")

    if rem_igst_bal > Decimal("0.00"):
        if offset_order == "EQUAL":
            half = (rem_igst_bal / Decimal("2.00")).quantize(Decimal("0.01"))
            igst_used_for_cgst = min(half, rem_cgst_liab)
            rem_igst_bal -= igst_used_for_cgst
            rem_cgst_liab -= igst_used_for_cgst

            igst_used_for_sgst = min(rem_igst_bal, rem_sgst_liab)
            rem_igst_bal -= igst_used_for_sgst
            rem_sgst_liab -= igst_used_for_sgst
        elif offset_order == "SGST_THEN_CGST":
            igst_used_for_sgst = min(rem_igst_bal, rem_sgst_liab)
            rem_igst_bal -= igst_used_for_sgst
            rem_sgst_liab -= igst_used_for_sgst

            igst_used_for_cgst = min(rem_igst_bal, rem_cgst_liab)
            rem_igst_bal -= igst_used_for_cgst
            rem_cgst_liab -= igst_used_for_cgst
        elif isinstance(offset_order, dict):
            cgst_req = Decimal(str(igst_offset_order.get("cgst") or 0))
            sgst_req = Decimal(str(igst_offset_order.get("sgst") or 0))
            igst_used_for_cgst = min(min(rem_igst_bal, cgst_req), rem_cgst_liab)
            rem_igst_bal -= igst_used_for_cgst
            rem_cgst_liab -= igst_used_for_cgst

            igst_used_for_sgst = min(min(rem_igst_bal, sgst_req), rem_sgst_liab)
            rem_igst_bal -= igst_used_for_sgst
            rem_sgst_liab -= igst_used_for_sgst
        else:  # CGST_THEN_SGST (default)
            igst_used_for_cgst = min(rem_igst_bal, rem_cgst_liab)
            rem_igst_bal -= igst_used_for_cgst
            rem_cgst_liab -= igst_used_for_cgst

            igst_used_for_sgst = min(rem_igst_bal, rem_sgst_liab)
            rem_igst_bal -= igst_used_for_sgst
            rem_sgst_liab -= igst_used_for_sgst

        if igst_used_for_cgst > Decimal("0.00"):
            debit_history.append({
                "step": 2,
                "action": "REMAINING_IGST_FOR_CGST_LIABILITY",
                "debit_amount": igst_used_for_cgst,
                "credit_head_used": "IGST",
                "liability_head_offset": "CGST",
                "remaining_igst_credit": rem_igst_bal,
                "remaining_cgst_liability": rem_cgst_liab
            })
        if igst_used_for_sgst > Decimal("0.00"):
            debit_history.append({
                "step": 2,
                "action": "REMAINING_IGST_FOR_SGST_LIABILITY",
                "debit_amount": igst_used_for_sgst,
                "credit_head_used": "IGST",
                "liability_head_offset": "SGST",
                "remaining_igst_credit": rem_igst_bal,
                "remaining_sgst_liability": rem_sgst_liab
            })

    # Rule 88A Exhaustion Gate Check:
    rule_88a_exhaustion_satisfied = (rem_igst_bal == Decimal("0.00")) or (rem_cgst_liab == Decimal("0.00") and rem_sgst_liab == Decimal("0.00"))

    # Step 3: Offset remaining CGST liability with CGST credit
    cgst_used_for_cgst = min(rem_cgst_bal, rem_cgst_liab)
    rem_cgst_bal -= cgst_used_for_cgst
    rem_cgst_liab -= cgst_used_for_cgst
    if cgst_used_for_cgst > Decimal("0.00"):
        debit_history.append({
            "step": 3,
            "action": "CGST_CREDIT_FOR_CGST_LIABILITY",
            "debit_amount": cgst_used_for_cgst,
            "credit_head_used": "CGST",
            "liability_head_offset": "CGST",
            "remaining_cgst_credit": rem_cgst_bal,
            "remaining_cgst_liability": rem_cgst_liab
        })

    # Step 4: Offset remaining SGST liability with SGST credit
    sgst_used_for_sgst = min(rem_sgst_bal, rem_sgst_liab)
    rem_sgst_bal -= sgst_used_for_sgst
    rem_sgst_liab -= sgst_used_for_sgst
    if sgst_used_for_sgst > Decimal("0.00"):
        debit_history.append({
            "step": 4,
            "action": "SGST_CREDIT_FOR_SGST_LIABILITY",
            "debit_amount": sgst_used_for_sgst,
            "credit_head_used": "SGST",
            "liability_head_offset": "SGST",
            "remaining_sgst_credit": rem_sgst_bal,
            "remaining_sgst_liability": rem_sgst_liab
        })

    # Step 5: If IGST liability still remains, offset with CGST credit then SGST credit
    cgst_used_for_igst = min(rem_cgst_bal, rem_igst_liab)
    rem_cgst_bal -= cgst_used_for_igst
    rem_igst_liab -= cgst_used_for_igst
    if cgst_used_for_igst > Decimal("0.00"):
        debit_history.append({
            "step": 5,
            "action": "CGST_CREDIT_FOR_REMAINING_IGST_LIABILITY",
            "debit_amount": cgst_used_for_igst,
            "credit_head_used": "CGST",
            "liability_head_offset": "IGST",
            "remaining_cgst_credit": rem_cgst_bal,
            "remaining_igst_liability": rem_igst_liab
        })

    sgst_used_for_igst = min(rem_sgst_bal, rem_igst_liab)
    rem_sgst_bal -= sgst_used_for_igst
    rem_igst_liab -= sgst_used_for_igst
    if sgst_used_for_igst > Decimal("0.00"):
        debit_history.append({
            "step": 5,
            "action": "SGST_CREDIT_FOR_REMAINING_IGST_LIABILITY",
            "debit_amount": sgst_used_for_igst,
            "credit_head_used": "SGST",
            "liability_head_offset": "IGST",
            "remaining_sgst_credit": rem_sgst_bal,
            "remaining_igst_liability": rem_igst_liab
        })

    # Step 6: Cess
    cess_used_for_cess = min(rem_cess_bal, rem_cess_liab)
    rem_cess_bal -= cess_used_for_cess
    rem_cess_liab -= cess_used_for_cess
    if cess_used_for_cess > Decimal("0.00"):
        debit_history.append({
            "step": 6,
            "action": "CESS_CREDIT_FOR_CESS_LIABILITY",
            "debit_amount": cess_used_for_cess,
            "credit_head_used": "CESS",
            "liability_head_offset": "CESS",
            "remaining_cess_credit": rem_cess_bal,
            "remaining_cess_liability": rem_cess_liab
        })

    tot_cash = rem_igst_liab + rem_cgst_liab + rem_sgst_liab + rem_cess_liab
    tot_utilized = (
        igst_used_for_igst + igst_used_for_cgst + igst_used_for_sgst +
        cgst_used_for_cgst + cgst_used_for_igst + sgst_used_for_sgst +
        sgst_used_for_igst + cess_used_for_cess
    )

    # Standardize debit history keys
    for entry in debit_history:
        entry["credit_head"] = entry.get("credit_head_used")
        entry["liability_head"] = entry.get("liability_head_offset")

    return {
        "opening_balances": {"igst": b_igst, "cgst": b_cgst, "sgst": b_sgst, "cess": b_cess},
        "output_liabilities": {"igst": l_igst, "cgst": l_cgst, "sgst": l_sgst, "cess": l_cess},
        "credits_utilized": {
            "igst_used_for_igst": igst_used_for_igst,
            "igst_used_for_cgst": igst_used_for_cgst,
            "igst_used_for_sgst": igst_used_for_sgst,
            "cgst_used_for_cgst": cgst_used_for_cgst,
            "cgst_used_for_igst": cgst_used_for_igst,
            "sgst_used_for_sgst": sgst_used_for_sgst,
            "sgst_used_for_igst": sgst_used_for_igst,
            "cess_used_for_cess": cess_used_for_cess,
            "total_itc_utilized": tot_utilized
        },
        "closing_balances": {
            "igst": rem_igst_bal,
            "cgst": rem_cgst_bal,
            "sgst": rem_sgst_bal,
            "cess": rem_cess_bal,
            "total": rem_igst_bal + rem_cgst_bal + rem_sgst_bal + rem_cess_bal
        },
        "remaining_itc": {
            "IGST": rem_igst_bal,
            "CGST": rem_cgst_bal,
            "SGST": rem_sgst_bal,
            "CESS": rem_cess_bal
        },
        "remaining_liabilities_payable_in_cash": {
            "igst": rem_igst_liab,
            "cgst": rem_cgst_liab,
            "sgst": rem_sgst_liab,
            "cess": rem_cess_liab,
            "total": tot_cash
        },
        "net_cash_payable": {
            "IGST": rem_igst_liab,
            "CGST": rem_cgst_liab,
            "SGST": rem_sgst_liab,
            "CESS": rem_cess_liab
        },
        "total_cash_payable": tot_cash,
        "total_itc_utilized": tot_utilized,
        "debit_history": debit_history,
        "rule_88a_exhaustion_satisfied": rule_88a_exhaustion_satisfied,
        "cross_head_double_counting_prevented": True,
        "statutory_authority": "Sections 49, 49A, 49B of CGST Act read with Rule 88A CGST Rules"
    }


def evaluate_section_17_5_vehicle_and_repair_eligibility(
    category_or_desc: str,
    hsn_sac: Optional[str] = None,
    facts: Optional[Dict[str, Any]] = None,
    business_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Statutory evaluation of Section 17(5)(a) and Section 17(5)(ab) of the CGST Act, 2017.

    STATUTORY PROVISIONS:
    1. Section 17(5)(a): ITC is BLOCKED on motor vehicles for transportation of persons
       having approved seating capacity of NOT MORE THAN 13 PERSONS (including the driver),
       EXCEPT when used for making the following taxable supplies:
       (A) further supply of such motor vehicles;
       (B) transportation of passengers;
       (C) imparting training on driving such motor vehicles.

       - Boundary condition:
         * Seating capacity <= 13 (including exactly 13 seats): Within blocked class.
         * Seating capacity > 13 (i.e. >= 14 persons including driver): EXEMPT from 17(5)(a) block (ELIGIBLE).

       - Goods Transport Vehicles:
         * Trucks, lorries, HSN 8704: Not vehicles for transportation of persons; outside 17(5)(a) block entirely.

    2. Section 17(5)(ab): General insurance, servicing, repair and maintenance follow the vehicle's
       eligibility under Section 17(5)(a).

    3. FACTUAL CONTEXT & EVIDENCE GATING:
       - A business category label alone without verified vehicle evidence CANNOT infer eligibility.
       - Missing or ambiguous facts MUST route to REVIEW_REQUIRED_FACTS_PENDING.
    """
    facts = facts or {}
    business_profile = business_profile or {}
    cat_lower = (category_or_desc or "").lower()
    hsn = str(hsn_sac or "").strip()

    is_repair_or_insurance = (
        hsn.startswith(("9987", "9971")) or
        any(k in cat_lower for k in ["repair", "servicing", "insurance", "maintenance"])
    )
    is_motor_vehicle = (
        hsn.startswith(("8702", "8703", "8704", "8711")) or
        any(k in cat_lower for k in ["motor", "vehicle", "car", "bus", "truck"])
    )

    if not (is_repair_or_insurance or is_motor_vehicle):
        return {"applies": False, "status": "NOT_APPLICABLE"}

    # Goods Transport Vehicles (e.g. HSN 8704, trucks, tippers)
    vehicle_type = (facts.get("vehicle_type") or "").lower()
    if hsn.startswith("8704") or "goods" in vehicle_type or facts.get("is_goods_transport_vehicle"):
        if facts.get("is_factual_context_verified", False):
            return {
                "applies": True,
                "is_blocked": False,
                "status": "ELIGIBLE_STATUTORY_EXCEPTION",
                "statutory_citation": "Section 17(5)(a) inapplicable: Goods transport vehicle (not for transportation of persons).",
                "seating_capacity": facts.get("seating_capacity")
            }
        else:
            return {
                "applies": True,
                "is_blocked": None,
                "status": "REVIEW_REQUIRED_FACTS_PENDING",
                "statutory_citation": "Section 17(5)(a): Goods transport claimed but vehicle RC / registration unverified.",
                "missing_evidence_required": "Provide verified RC copy confirming goods carriage category."
            }

    # If facts are explicitly provided, check verification state
    if facts:
        is_verified = facts.get("is_factual_context_verified", False)
        if not is_verified:
            return {
                "applies": True,
                "is_blocked": None,
                "status": "REVIEW_REQUIRED_FACTS_PENDING",
                "statutory_citation": "Section 17(5)(a) / Section 17(5)(ab): Factual context, seating capacity, and vehicle usage unverified.",
                "missing_evidence_required": "Provide vehicle registration certificate (RC), approved seating capacity, and end-use declaration."
            }

        # Seating Capacity Evaluation (> 13 vs <= 13)
        seating_capacity = facts.get("seating_capacity")
        if seating_capacity is not None:
            try:
                seats = int(seating_capacity)
                if seats > 13:
                    # Strictly MORE THAN 13 persons (e.g. 14, 25, 50 seats)
                    return {
                        "applies": True,
                        "is_blocked": False,
                        "status": "ELIGIBLE_STATUTORY_EXCEPTION",
                        "statutory_citation": f"Section 17(5)(a) exception: Approved seating capacity of {seats} persons is MORE THAN 13 persons.",
                        "seating_capacity": seats
                    }
                # If seats <= 13 (including exactly 13 seats), falls under blocked class of "not more than 13 persons"
            except (ValueError, TypeError):
                pass

        # Specified Purpose Exceptions under Section 17(5)(a)(A), (B), (C)
        if facts.get("further_supply_of_vehicles") or facts.get("is_car_dealer"):
            return {
                "applies": True,
                "is_blocked": False,
                "status": "ELIGIBLE_STATUTORY_EXCEPTION",
                "statutory_citation": "Section 17(5)(a)(A) exception: Further taxable supply of such motor vehicles.",
                "seating_capacity": seating_capacity
            }

        if facts.get("passenger_transport_license") or facts.get("is_passenger_transport_business"):
            if facts.get("passenger_transport_license"):
                return {
                    "applies": True,
                    "is_blocked": False,
                    "status": "ELIGIBLE_STATUTORY_EXCEPTION",
                    "statutory_citation": "Section 17(5)(a)(B) exception: Verified commercial passenger transportation permit.",
                    "seating_capacity": seating_capacity
                }
            else:
                return {
                    "applies": True,
                    "is_blocked": None,
                    "status": "REVIEW_REQUIRED_FACTS_PENDING",
                    "statutory_citation": "Section 17(5)(a)(B): Passenger transport claimed without verified commercial transport permit.",
                    "missing_evidence_required": "Provide commercial passenger transport permit / stage carriage permit."
                }

        if facts.get("is_driving_school_training"):
            return {
                "applies": True,
                "is_blocked": False,
                "status": "ELIGIBLE_STATUTORY_EXCEPTION",
                "statutory_citation": "Section 17(5)(a)(C) exception: Imparting training on driving such motor vehicles.",
                "seating_capacity": seating_capacity
            }

        seats_str = f" of {seating_capacity} persons" if seating_capacity is not None else ""
        return {
            "applies": True,
            "is_blocked": True,
            "status": "BLOCKED_CREDIT_CONFIRMED",
            "statutory_citation": f"Section 17(5)(a) / 17(5)(ab): Motor vehicle seating capacity{seats_str} is NOT MORE THAN 13 persons and no statutory exception applies.",
            "seating_capacity": seating_capacity
        }

    # Default if no facts dictionary provided at all (e.g. bare invoice description)
    return {
        "applies": True,
        "is_blocked": True,
        "status": "BLOCKED_CREDIT_INDICATOR",
        "statutory_citation": "Motor vehicle with seating capacity <= 13 persons is blocked under Section 17(5)(a).",
        "seating_capacity": None
    }


class StatutoryITCRiskEngine:
    """
    Milestone 2 Versioned Statutory ITC Risk & Eligibility Engine.
    Evaluates CGST Section 16, Section 17, Rule 37, Rule 37A, and RCM rules.
    """

    def __init__(self, engine_version: str = "v2.1_statutory"):
        self.engine_version = engine_version

    def evaluate_itc_eligibility(
        self,
        purchase_doc: Dict[str, Any],
        gstr2b_doc: Optional[Dict[str, Any]] = None,
        payments: Optional[List[Dict[str, Any]]] = None,
        evaluation_date: Optional[date] = None,
        ecl_minimum_balance: Optional[Decimal] = None,
        utilisation_date: Optional[date] = None,
        reversal_date: Optional[date] = None,
        annual_return_filing_date: Optional[date] = None
    ) -> List[ITCAssessmentOut]:
        eval_dt = evaluation_date or date.today()
        assessments: List[ITCAssessmentOut] = []

        p_id = str(purchase_doc["id"])
        g_id = str(gstr2b_doc["id"]) if gstr2b_doc else None

        p_date_raw = purchase_doc.get("document_date")
        p_date = datetime.strptime(str(p_date_raw)[:10], "%Y-%m-%d").date() if p_date_raw else eval_dt
        days_elapsed = (eval_dt - p_date).days

        cgst = Decimal(str(purchase_doc.get("cgst_amount") or 0))
        sgst = Decimal(str(purchase_doc.get("sgst_amount") or 0))
        igst = Decimal(str(purchase_doc.get("igst_amount") or 0))
        cess = Decimal(str(purchase_doc.get("cess_amount") or 0))
        total_tax = cgst + sgst + igst + cess
        total_inv_val = Decimal(str(purchase_doc.get("total_invoice_value") or 0))

        # -------------------------------------------------------------
        # 1. Section 16(2)(aa): Supplier Reporting in GSTR-1 & Inclusion in GSTR-2B
        # -------------------------------------------------------------
        if not gstr2b_doc:
            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=None,
                rule_code="SEC_16_2_AA",
                statutory_section="Section 16(2)(aa)",
                eligibility_status=ITCEligibilityEnum.SUPPLIER_STATUS_UNKNOWN,
                potential_itc=total_tax,
                risk_reversal_amount=total_tax,
                findings_explanation="Document not found in GSTR-2B. ITC cannot be claimed under Section 16(2)(aa) until communicated by supplier.",
                missing_evidence_required="Supplier must upload invoice to GSTR-1/IFF and generate GSTR-2B entry.",
                evaluation_date=str(eval_dt)
            ))
        elif gstr2b_doc.get("itc_availability") == "not_available":
            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=g_id,
                rule_code="SEC_16_2_AA",
                statutory_section="Section 16(2)(aa)",
                eligibility_status=ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR,
                potential_itc=total_tax,
                risk_reversal_amount=total_tax,
                findings_explanation="Document is flagged as 'ITC Not Available' in downloaded GSTR-2B statement (e.g. POS restriction or supplier filing limit).",
                missing_evidence_required="Review supplier filing state and place of supply conditions.",
                evaluation_date=str(eval_dt)
            ))

        # -------------------------------------------------------------
        # 2. Section 16(2)(b): Receipt of Goods or Services Evidence
        # -------------------------------------------------------------
        goods_receipt_date = purchase_doc.get("goods_receipt_date")
        if not goods_receipt_date:
            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=g_id,
                rule_code="SEC_16_2_B",
                statutory_section="Section 16(2)(b)",
                eligibility_status=ITCEligibilityEnum.PENDING_GOODS_OR_SERVICE_RECEIPT,
                potential_itc=total_tax,
                risk_reversal_amount=Decimal("0.00"),
                findings_explanation="Goods Receipt Note (GRN) or proof of delivery is pending.",
                missing_evidence_required="Attach Good Receipt Note, delivery challan, or service completion sign-off.",
                evaluation_date=str(eval_dt)
            ))

        # -------------------------------------------------------------
        # 3. Section 16(4): Statutory Time Limit (30th November following FY or Annual Return, whichever is earlier)
        # -------------------------------------------------------------
        inv_year = p_date.year
        inv_month = p_date.month
        # FY end is March 31 of current/next year
        fy_end_year = inv_year if inv_month <= 3 else inv_year + 1
        statutory_nov30 = date(fy_end_year, 11, 30)

        # Section 16(4) applies "whichever is earlier"
        if annual_return_filing_date and annual_return_filing_date < statutory_nov30:
            cutoff_date = annual_return_filing_date
            deadline_source = f"earlier GSTR-9 Annual Return filing date of {annual_return_filing_date} (whichever is earlier per Section 16(4))"
        else:
            cutoff_date = statutory_nov30
            deadline_source = f"30th November {fy_end_year}"

        if eval_dt > cutoff_date:
            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=g_id,
                rule_code="SEC_16_4",
                statutory_section="Section 16(4)",
                eligibility_status=ITCEligibilityEnum.TIME_LIMIT_RISK,
                potential_itc=total_tax,
                risk_reversal_amount=total_tax,
                findings_explanation=f"Invoice date {p_date} exceeds Section 16(4) statutory time limit of {deadline_source}.",
                missing_evidence_required="Check whether special statutory extension or Section 16(5)/16(6) retrospective relief applies.",
                evaluation_date=str(eval_dt)
            ))

        # -------------------------------------------------------------
        # 4. Rule 37: 180-Day Vendor Payment Tracking
        # -------------------------------------------------------------
        total_paid = Decimal("0.00")
        if payments:
            for p in payments:
                total_paid += Decimal(str(p.get("allocated_total_amount") or 0))

        unpaid = max(Decimal("0.00"), total_inv_val - total_paid)
        is_reverse_charge = purchase_doc.get("is_reverse_charge", False)

        # Rule 37 applies only to forward charge supplies and where ITC was availed in GSTR-3B
        is_availed_in_3b = purchase_doc.get("is_availed_in_3b")
        if is_availed_in_3b is False:
            logger.debug("Purchase doc %s not availed in GSTR-3B; Rule 37 reversal not triggered.", p_id)
        elif not is_reverse_charge and days_elapsed > 180 and unpaid > Decimal("0.00"):
            # Proportionate reversal
            prop_ratio = unpaid / total_inv_val if total_inv_val > 0 else Decimal("1.00")
            reversal_tax = (total_tax * prop_ratio).quantize(Decimal("0.01"))
            days_over = days_elapsed - 180

            if ecl_minimum_balance is not None:
                int_calc = calculate_rule_37_interest(
                    reversal_tax=reversal_tax,
                    ecl_minimum_balance=ecl_minimum_balance,
                    utilisation_date=utilisation_date,
                    reversal_date=reversal_date or eval_dt
                )
                interest_estimate = int_calc["interest_amount"]
                explanation = (
                    f"Vendor invoice unpaid after {days_elapsed} days ({days_over} days past 180-day limit). "
                    f"Proportionate reversal required: ₹{reversal_tax}. Estimated interest under Section 50(1) at 18% p.a.: ₹{interest_estimate}. "
                    f"ECL utilization basis: {int_calc['explanation']}"
                )
            else:
                interest_estimate = (reversal_tax * Decimal("0.18") * Decimal(days_over) / Decimal("365")).quantize(Decimal("0.01"))
                explanation = f"Vendor invoice unpaid after {days_elapsed} days ({days_over} days past 180-day limit). Proportionate reversal required: ₹{reversal_tax}. Estimated interest under Section 50(1) at 18% p.a.: ₹{interest_estimate}."

            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=g_id,
                rule_code="RULE_37_180_DAYS",
                statutory_section="Rule 37",
                eligibility_status=ITCEligibilityEnum.PAYMENT_180_DAY_RISK,
                potential_itc=total_tax,
                risk_reversal_amount=reversal_tax,
                findings_explanation=explanation,
                missing_evidence_required="Record payment voucher, bank settlement, or formal credit adjustment to re-avail credit.",
                evaluation_date=str(eval_dt)
            ))

        # -------------------------------------------------------------
        # 5. Rule 37A: Supplier GSTR-3B Non-Filing Statutory Assessment
        # -------------------------------------------------------------
        if gstr2b_doc:
            source_payload = gstr2b_doc.get("raw_payload") or {}
            gstr3b_status_raw = source_payload.get("gstr3b_status")
            gstr3b_status = str(gstr3b_status_raw).upper() if gstr3b_status_raw else "UNKNOWN"
            is_availed_in_3b = purchase_doc.get("is_availed_in_3b", False)

            if not is_availed_in_3b:
                # Rule 37A explicitly applies only where ITC was availed by recipient in GSTR-3B
                logger.debug("Purchase doc %s not availed in GSTR-3B; Rule 37A reversal not triggered.", p_id)
            else:
                # Rule 37A Financial Year determination tied to FY in which credit was AVAILED
                av_period = purchase_doc.get("availed_period") or purchase_doc.get("financial_period")
                if av_period and len(str(av_period)) >= 7:
                    try:
                        av_year = int(str(av_period)[:4])
                        av_month = int(str(av_period)[5:7])
                    except (ValueError, IndexError):
                        av_year, av_month = inv_year, inv_month
                else:
                    av_year, av_month = inv_year, inv_month

                # End of FY in which ITC was availed (e.g. 2026-02 -> March 2026; 2026-05 -> March 2027)
                fy_end_year = av_year if av_month <= 3 else av_year + 1
                supplier_filing_deadline = date(fy_end_year, 9, 30)
                recipient_reversal_deadline = date(fy_end_year, 11, 30)

                if gstr3b_status == "UNKNOWN":
                    # Do not assume non-compliance when authoritative portal data is missing
                    assessments.append(ITCAssessmentOut(
                        purchase_doc_id=p_id,
                        gstr2b_doc_id=g_id,
                        rule_code="RULE_37A_NON_FILING",
                        statutory_section="Rule 37A",
                        eligibility_status=ITCEligibilityEnum.SUPPLIER_STATUS_UNKNOWN,
                        potential_itc=total_tax,
                        risk_reversal_amount=Decimal("0.00"),
                        findings_explanation="Authoritative supplier GSTR-3B filing status is unknown/uncommunicated by GST portal. No reversal assessed pending verification.",
                        missing_evidence_required="Obtain supplier GSTR-3B filing acknowledgement or portal confirmation.",
                        evaluation_date=str(eval_dt)
                    ))
                elif gstr3b_status == "NOT_FILED":
                    if eval_dt <= supplier_filing_deadline:
                        # Before 30th September of following FY: supplier still has statutory time
                        assessments.append(ITCAssessmentOut(
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            rule_code="RULE_37A_NON_FILING",
                            statutory_section="Rule 37A",
                            eligibility_status=ITCEligibilityEnum.POTENTIALLY_ELIGIBLE,
                            potential_itc=total_tax,
                            risk_reversal_amount=Decimal("0.00"),
                            findings_explanation=f"Supplier GSTR-3B is currently unfiled, but supplier has until statutory cut-off 30th September {fy_end_year} to furnish return.",
                            missing_evidence_required="Monitor supplier GSTR-3B filing until 30th September cut-off.",
                            evaluation_date=str(eval_dt)
                        ))
                    elif eval_dt <= recipient_reversal_deadline:
                        # Between 1st Oct and 30th Nov: reversal required; no interest under proviso
                        assessments.append(ITCAssessmentOut(
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            rule_code="RULE_37A_NON_FILING",
                            statutory_section="Rule 37A",
                            eligibility_status=ITCEligibilityEnum.RULE_37A_RISK,
                            potential_itc=total_tax,
                            risk_reversal_amount=total_tax,
                            findings_explanation=f"Supplier failed to furnish GSTR-3B by statutory cut-off 30th September {fy_end_year}. Mandatory reversal of ₹{total_tax} required in GSTR-3B on or before 30th November {fy_end_year}. No interest payable under Rule 37A proviso if reversed before deadline.",
                            missing_evidence_required="Reverse credit in Form GSTR-3B Table 4(B)(2) on or before 30th November.",
                            evaluation_date=str(eval_dt)
                        ))
                    else:
                        # After 30th Nov: reversal required along with 18% p.a. interest under Section 50(3)
                        days_delayed = (eval_dt - recipient_reversal_deadline).days
                        interest_liability = (total_tax * Decimal("0.18") * Decimal(days_delayed) / Decimal("365")).quantize(Decimal("0.01"))
                        assessments.append(ITCAssessmentOut(
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            rule_code="RULE_37A_NON_FILING",
                            statutory_section="Rule 37A",
                            eligibility_status=ITCEligibilityEnum.RULE_37A_RISK,
                            potential_itc=total_tax,
                            risk_reversal_amount=total_tax,
                            findings_explanation=f"Supplier unfiled past 30th September {fy_end_year}. Recipient reversal deadline 30th November {fy_end_year} exceeded by {days_delayed} days. Mandatory reversal of ₹{total_tax} plus Section 50 interest at 18% p.a. (estimated ₹{interest_liability}) under Section 50(3). Note: Under Section 50(3) read with Rule 88B(3), interest applies only if credit was actually utilized against output tax liability.",
                            missing_evidence_required="Pay reversed ITC under Table 4(B)(2) and evaluate Section 50(3) interest based on Electronic Credit Ledger utilization.",
                            evaluation_date=str(eval_dt)
                        ))
                elif gstr3b_status == "FILED":
                    # Supplier filed return; check if previously reversed for re-availment
                    if purchase_doc.get("status") == "reversed" or purchase_doc.get("previously_reversed_under_37a"):
                        assessments.append(ITCAssessmentOut(
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            rule_code="RULE_37A_NON_FILING",
                            statutory_section="Rule 37A",
                            eligibility_status=ITCEligibilityEnum.REAVAILMENT_REVIEW_REQUIRED,
                            potential_itc=total_tax,
                            risk_reversal_amount=Decimal("0.00"),
                            findings_explanation="Supplier has subsequently furnished GSTR-3B return. Recipient is legally eligible to re-avail previously reversed ITC under Rule 37A second proviso in Form GSTR-3B Table 4(A)(5) and report in 4(D)(1).",
                            missing_evidence_required="Record re-availment in current period GSTR-3B Table 4(A)(5) and 4(D)(1).",
                            evaluation_date=str(eval_dt)
                        ))

        # -------------------------------------------------------------
        # 6. Section 17(5): Blocked Credit Catalogue Checks
        # -------------------------------------------------------------
        desc = (purchase_doc.get("goods_services_type") or "").lower()
        ledger = (purchase_doc.get("purchase_ledger_code") or "").lower()
        top_desc = (purchase_doc.get("item_description") or "").lower()
        items = purchase_doc.get("line_items") or []
        if not items and top_desc:
            items = [{"item_description": top_desc, "hsn_sac_code": purchase_doc.get("hsn_sac_code")}]

        for line in items:
            item_desc = (line.get("item_description") or "").lower()
            hsn = str(line.get("hsn_sac_code") or "")

            # Motor Vehicles 17(5)(a) & Repair/Maintenance 17(5)(ab)
            if (
                hsn.startswith(("8702", "8703", "8704", "8711", "9987", "9971")) or
                any(k in item_desc for k in ["motor car", "vehicle", "car", "bus", "truck", "servicing", "motor repair", "vehicle repair"])
            ):
                veh_facts = line.get("facts") or purchase_doc.get("facts") or {}
                veh_res = evaluate_section_17_5_vehicle_and_repair_eligibility(
                    category_or_desc=item_desc,
                    hsn_sac=hsn,
                    facts=veh_facts,
                    business_profile=purchase_doc.get("business_profile") or {}
                )
                if veh_res.get("applies"):
                    if veh_res.get("is_blocked") is True:
                        assessments.append(ITCAssessmentOut(
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            rule_code="SEC_17_5_MOTOR",
                            statutory_section="Section 17(5)(a)" if not hsn.startswith(("9987", "9971")) else "Section 17(5)(ab)",
                            eligibility_status=ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR,
                            potential_itc=total_tax,
                            risk_reversal_amount=total_tax,
                            findings_explanation=veh_res.get("statutory_citation", "Motor vehicle with seating capacity <= 13 persons is blocked under Section 17(5)(a)."),
                            missing_evidence_required="Provide proof of passenger transport business permit, driving school, or seating capacity > 13 persons.",
                            evaluation_date=str(eval_dt)
                        ))
                        break
                    elif veh_res.get("status") == "REVIEW_REQUIRED_FACTS_PENDING":
                        assessments.append(ITCAssessmentOut(
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            rule_code="SEC_17_5_MOTOR",
                            statutory_section="Section 17(5)(a)" if not hsn.startswith(("9987", "9971")) else "Section 17(5)(ab)",
                            eligibility_status=ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR,
                            potential_itc=total_tax,
                            risk_reversal_amount=total_tax,
                            findings_explanation=veh_res.get("statutory_citation", "Vehicle context and seating capacity unverified."),
                            missing_evidence_required=veh_res.get("missing_evidence_required", "Provide verified RC copy confirming seating capacity and business end-use."),
                            evaluation_date=str(eval_dt)
                        ))
                        break
                    elif veh_res.get("is_blocked") is False:
                        # Statutorily exempt from block (e.g. seating capacity > 13 or goods transport vehicle)
                        break

            # Food & Beverages 17(5)(b) - HSN 9963
            elif hsn.startswith("9963") or "catering" in item_desc or "food" in item_desc:
                assessments.append(ITCAssessmentOut(
                    purchase_doc_id=p_id,
                    gstr2b_doc_id=g_id,
                    rule_code="SEC_17_5_FOOD",
                    statutory_section="Section 17(5)(b)",
                    eligibility_status=ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR,
                    potential_itc=total_tax,
                    risk_reversal_amount=total_tax,
                    findings_explanation="Food, beverages, and outdoor catering are blocked under Section 17(5)(b).",
                    missing_evidence_required="Provide evidence of outward taxable supply of same category or employer statutory obligation.",
                    evaluation_date=str(eval_dt)
                ))
                break

            # Works Contract 17(5)(c) - HSN 9954
            elif hsn.startswith("9954") or "works contract" in item_desc or "construction" in item_desc:
                assessments.append(ITCAssessmentOut(
                    purchase_doc_id=p_id,
                    gstr2b_doc_id=g_id,
                    rule_code="SEC_17_5_WORKS_CONTR",
                    statutory_section="Section 17(5)(c)",
                    eligibility_status=ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR,
                    potential_itc=total_tax,
                    risk_reversal_amount=total_tax,
                    findings_explanation="Works contract for construction of immovable property is blocked under Section 17(5)(c).",
                    missing_evidence_required="Proof that supply is for plant and machinery or outward works contract supply.",
                    evaluation_date=str(eval_dt)
                ))
                break

            # Personal Consumption 17(5)(g)
            elif "personal" in item_desc or "gift" in item_desc:
                assessments.append(ITCAssessmentOut(
                    purchase_doc_id=p_id,
                    gstr2b_doc_id=g_id,
                    rule_code="SEC_17_5_PERSONAL",
                    statutory_section="Section 17(5)(g)",
                    eligibility_status=ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR,
                    potential_itc=total_tax,
                    risk_reversal_amount=total_tax,
                    findings_explanation="Goods or services for personal consumption or gifts are blocked under Section 17(5)(g).",
                    missing_evidence_required="Proof of direct business nexus.",
                    evaluation_date=str(eval_dt)
                ))
                break

        # -------------------------------------------------------------
        # 7. Anti-Conflation Control: Rule 37 vs Rule 37A Duplicate Reversal Check
        # -------------------------------------------------------------
        rule_37_assessments = [a for a in assessments if a.rule_code == "RULE_37_180_DAYS"]
        rule_37a_assessments = [a for a in assessments if a.rule_code == "RULE_37A_NON_FILING"]
        # Check for assessments that actually require reversal (not just UNKNOWN status)
        rule_37_reversal = [a for a in rule_37_assessments if a.risk_reversal_amount > Decimal("0.00")]
        rule_37a_reversal = [a for a in rule_37a_assessments
                             if a.risk_reversal_amount > Decimal("0.00")
                             and a.eligibility_status == ITCEligibilityEnum.RULE_37A_RISK]

        if rule_37_reversal and rule_37a_reversal:
            r37_amt = rule_37_reversal[0].risk_reversal_amount
            r37a_amt = rule_37a_reversal[0].risk_reversal_amount
            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=g_id,
                rule_code="DUPLICATE_REVERSAL_RISK",
                statutory_section="Rule 37 / Rule 37A Anti-Conflation",
                eligibility_status=ITCEligibilityEnum.REVIEW_REQUIRED,
                potential_itc=total_tax,
                risk_reversal_amount=max(r37_amt, r37a_amt),
                findings_explanation=(
                    f"DUPLICATE REVERSAL RISK: Both Rule 37 (180-day non-payment, reversal ₹{r37_amt}) "
                    f"and Rule 37A (supplier GSTR-3B non-filing, reversal ₹{r37a_amt}) are triggered "
                    f"for the same purchase document {p_id}. These are INDEPENDENT statutory tests with "
                    f"DIFFERENT triggers — 180-day non-payment does NOT imply supplier GSTR-3B non-filing "
                    f"and vice versa. However, the SAME ITC credit must NOT be reversed twice. "
                    f"The maximum single reversal amount is ₹{max(r37_amt, r37a_amt)}. "
                    f"Manual review required to determine which rule governs and to prevent double reversal."
                ),
                missing_evidence_required=(
                    "Tax professional must determine: (1) which rule triggered first chronologically, "
                    "(2) whether the same ITC has already been reversed under either rule, and "
                    "(3) the correct single reversal amount to post in Table 4(B)(2)."
                ),
                evaluation_date=str(eval_dt)
            ))

        # -------------------------------------------------------------
        # 8. Default to Complete Eligibility if all tests pass
        # -------------------------------------------------------------
        if not assessments:
            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=g_id,
                rule_code="SEC_16_COMPLETE",
                statutory_section="Section 16(2)",
                eligibility_status=ITCEligibilityEnum.ELIGIBLE_EVIDENCE_COMPLETE,
                potential_itc=total_tax,
                risk_reversal_amount=Decimal("0.00"),
                findings_explanation="All Section 16 statutory conditions satisfied: in GSTR-2B, valid document, and within payment limits.",
                missing_evidence_required=None,
                evaluation_date=str(eval_dt)
            ))

        return assessments

    def evaluate_rule_37a_details(
        self,
        purchase_doc: Dict[str, Any],
        gstr2b_doc: Optional[Dict[str, Any]] = None,
        evaluation_date: Optional[date] = None
    ) -> Rule37AFilingCheck:
        """
        Detailed statutory evaluation of Rule 37A with separate 30-Sept supplier cut-off
        and 30-Nov recipient reversal deadline, un-availed exclusion, and 18% p.a. interest.
        """
        eval_dt = evaluation_date or date.today()
        p_id = str(purchase_doc["id"])
        supplier_gstin = str(purchase_doc.get("supplier_gstin") or "UNKNOWN")
        p_date_raw = purchase_doc.get("document_date")
        p_date = datetime.strptime(str(p_date_raw)[:10], "%Y-%m-%d").date() if p_date_raw else eval_dt

        inv_year = p_date.year
        inv_month = p_date.month

        # Rule 37A statutory deadlines tied to FY of availment:
        av_period = purchase_doc.get("availed_period") or purchase_doc.get("financial_period")
        if av_period and len(str(av_period)) >= 7:
            try:
                av_year = int(str(av_period)[:4])
                av_month = int(str(av_period)[5:7])
            except (ValueError, IndexError):
                av_year, av_month = inv_year, inv_month
        else:
            av_year, av_month = inv_year, inv_month

        fy_end_year = av_year if av_month <= 3 else av_year + 1
        supplier_filing_deadline = date(fy_end_year, 9, 30)
        recipient_reversal_deadline = date(fy_end_year, 11, 30)

        source_payload = (gstr2b_doc.get("raw_payload") or {}) if gstr2b_doc else {}
        gstr3b_status_raw = source_payload.get("gstr3b_status")
        gstr3b_status = str(gstr3b_status_raw).upper() if gstr3b_status_raw else "UNKNOWN"
        is_availed_in_3b = purchase_doc.get("is_availed_in_3b", False)

        cgst = Decimal(str(purchase_doc.get("cgst_amount") or 0))
        sgst = Decimal(str(purchase_doc.get("sgst_amount") or 0))
        igst = Decimal(str(purchase_doc.get("igst_amount") or 0))
        cess = Decimal(str(purchase_doc.get("cess_amount") or 0))
        total_tax = cgst + sgst + igst + cess

        reversal_required = False
        reversal_amount = Decimal("0.00")
        interest_liability = Decimal("0.00")
        reavailment_eligible = False
        risk_status = ITCEligibilityEnum.ELIGIBLE_EVIDENCE_COMPLETE

        if not is_availed_in_3b:
            risk_status = ITCEligibilityEnum.ELIGIBLE_EVIDENCE_COMPLETE
        elif gstr3b_status == "UNKNOWN":
            risk_status = ITCEligibilityEnum.SUPPLIER_STATUS_UNKNOWN
        elif gstr3b_status == "NOT_FILED":
            if eval_dt <= supplier_filing_deadline:
                risk_status = ITCEligibilityEnum.POTENTIALLY_ELIGIBLE
            elif eval_dt <= recipient_reversal_deadline:
                reversal_required = True
                reversal_amount = total_tax
                risk_status = ITCEligibilityEnum.RULE_37A_RISK
            else:
                reversal_required = True
                reversal_amount = total_tax
                days_delayed = (eval_dt - recipient_reversal_deadline).days
                interest_liability = (total_tax * Decimal("0.18") * Decimal(days_delayed) / Decimal("365")).quantize(Decimal("0.01"))
                risk_status = ITCEligibilityEnum.RULE_37A_RISK
        elif gstr3b_status == "FILED":
            if purchase_doc.get("status") == "reversed" or purchase_doc.get("previously_reversed_under_37a"):
                reavailment_eligible = True
                risk_status = ITCEligibilityEnum.REAVAILMENT_REVIEW_REQUIRED

        return Rule37AFilingCheck(
            purchase_doc_id=p_id,
            supplier_gstin=supplier_gstin,
            invoice_date=str(p_date),
            gstr1_reported_period=gstr2b_doc.get("source_gstr1_filing_period", "") if gstr2b_doc else "",
            supplier_gstr3b_status=gstr3b_status,
            supplier_filing_deadline=str(supplier_filing_deadline),
            recipient_reversal_deadline=str(recipient_reversal_deadline),
            is_availed_in_3b=is_availed_in_3b,
            reversal_required=reversal_required,
            reversal_amount=reversal_amount,
            interest_liability=interest_liability,
            reavailment_eligible=reavailment_eligible,
            risk_status=risk_status
        )


def classify_reversal_trigger(
    invoice_date: date,
    availment_period: Optional[str] = None,
    payment_records: Optional[List[Dict[str, Any]]] = None,
    total_invoice_value: Decimal = Decimal("0.00"),
    total_tax_amount: Decimal = Decimal("0.00"),
    supplier_gstr3b_status: str = "UNKNOWN",
    supplier_gstr1_filed: bool = False,
    is_availed_in_3b: bool = True,
    evaluation_date: Optional[date] = None,
    audit_date: Optional[date] = None
) -> Dict[str, Any]:
    """
    Classifies whether an ITC reversal is triggered under Rule 37, Rule 37A, or both.
    
    Statutory Framework:
    1. Rule 37 (180-Day Non-Payment of Consideration):
       - Trigger: Recipient availed ITC but failed to pay supplier within 180 days from invoice date.
       - Reversal is proportionate to unpaid consideration.
       - Due period: Return period immediately following the 180-day expiry.
       - Applicable section: Second Proviso to Section 16(2) read with Rule 37 and Section 50(1).
       - Requires payment ledger / bank evidence.
       
    2. Rule 37A (Supplier GSTR-3B Non-Filing):
       - Trigger: Supplier reported invoice in GSTR-1/IFF but failed to furnish GSTR-3B by 30th Sept
         following the FY in which recipient availed ITC.
       - Recipient reversal is mandatory on or before 30th November following that FY.
       - Due period: Any return on or before 30th November (no interest under Rule 37A proviso if reversed timely).
       - Requires GSTN portal filing status evidence.
       
    3. Independence & Anti-Conflation:
       - 180-day non-payment does NOT trigger or imply Rule 37A.
       - Supplier GSTR-3B non-filing does NOT trigger or imply Rule 37.
       - Missing trigger evidence results in REVIEW_REQUIRED, not an assumed statutory conclusion.
       - If both rules apply independently to the same invoice, DUPLICATE_REVERSAL_RISK is flagged
         to prevent reversing the same ITC twice.
    """
    from datetime import timedelta
    eval_dt = evaluation_date or audit_date or date.today()
    tot_val = Decimal(str(total_invoice_value or 0))
    tot_tax = Decimal(str(total_tax_amount or 0))

    # --- Rule 37 Calculation ---
    day_180 = invoice_date + timedelta(days=180)
    day_181 = invoice_date + timedelta(days=181)
    days_elapsed = (eval_dt - invoice_date).days

    # Return period immediately following 180-day expiry:
    # If 180-day expires on day_180, the return for the tax period in which day_181 falls
    rule_37_reversal_period = day_180.strftime("%Y-%m")

    rule_37_applicable: Optional[bool] = None
    rule_37_reversal_amount = Decimal("0.00")
    rule_37_trigger_evidence: Dict[str, Any] = {
        "invoice_date": invoice_date,
        "day_180": day_180,
        "day_181": day_181,
        "days_elapsed": days_elapsed,
        "is_past_180_days": eval_dt >= day_181,
        "reversal_due_period": rule_37_reversal_period,
    }

    if not is_availed_in_3b:
        rule_37_applicable = False
        rule_37_trigger_evidence["reason"] = "ITC was never availed in GSTR-3B; Rule 37 does not apply."
    elif payment_records is None:
        rule_37_applicable = None
        rule_37_trigger_evidence["reason"] = "Payment records are unverified/missing; cannot determine payment status."
    else:
        total_paid = Decimal("0.00")
        for p in payment_records:
            total_paid += Decimal(str(p.get("allocated_total_amount") or 0))
        unpaid = max(Decimal("0.00"), tot_val - total_paid)
        rule_37_trigger_evidence["total_paid"] = total_paid
        rule_37_trigger_evidence["unpaid_amount"] = unpaid

        if eval_dt >= day_181 and unpaid > Decimal("0.00"):
            rule_37_applicable = True
            prop_ratio = (unpaid / tot_val) if tot_val > Decimal("0.00") else Decimal("1.00")
            rule_37_reversal_amount = (tot_tax * prop_ratio).quantize(Decimal("0.01"))
            rule_37_trigger_evidence["reversal_amount"] = rule_37_reversal_amount
            rule_37_trigger_evidence["reason"] = f"Unpaid amount ₹{unpaid} past 180 days ({days_elapsed} days elapsed)."
        else:
            rule_37_applicable = False
            if unpaid <= Decimal("0.00"):
                rule_37_trigger_evidence["reason"] = "Invoice fully paid; no Rule 37 reversal required."
            else:
                rule_37_trigger_evidence["reason"] = f"Invoice within 180 days ({days_elapsed} days elapsed; day 180 is {day_180})."

    # --- Rule 37A Calculation ---
    if availment_period and len(str(availment_period)) >= 7:
        try:
            av_year = int(str(availment_period)[:4])
            av_month = int(str(availment_period)[5:7])
        except (ValueError, IndexError):
            av_year, av_month = invoice_date.year, invoice_date.month
    else:
        av_year, av_month = invoice_date.year, invoice_date.month

    fy_end_year = av_year if av_month <= 3 else av_year + 1
    supplier_filing_deadline = date(fy_end_year, 9, 30)
    recipient_reversal_deadline = date(fy_end_year, 11, 30)

    gstr3b_status_norm = str(supplier_gstr3b_status or "UNKNOWN").upper()
    rule_37a_applicable: Optional[bool] = None
    rule_37a_reversal_amount = Decimal("0.00")
    rule_37a_trigger_evidence: Dict[str, Any] = {
        "availment_period": availment_period,
        "fy_end_year": fy_end_year,
        "supplier_30_sept_deadline": supplier_filing_deadline,
        "recipient_30_nov_deadline": recipient_reversal_deadline,
        "supplier_gstr3b_status": gstr3b_status_norm,
        "supplier_gstr1_filed": supplier_gstr1_filed,
    }

    if not is_availed_in_3b:
        rule_37a_applicable = False
        rule_37a_trigger_evidence["reason"] = "ITC was never availed in GSTR-3B; Rule 37A does not apply."
    elif gstr3b_status_norm == "UNKNOWN":
        rule_37a_applicable = None
        rule_37a_trigger_evidence["reason"] = "Authoritative supplier GSTR-3B filing status is unknown; cannot determine Rule 37A liability."
    elif gstr3b_status_norm == "FILED":
        rule_37a_applicable = False
        rule_37a_trigger_evidence["reason"] = "Supplier furnished GSTR-3B return; no Rule 37A reversal required."
    elif gstr3b_status_norm == "NOT_FILED":
        if eval_dt <= supplier_filing_deadline:
            rule_37a_applicable = False
            rule_37a_trigger_evidence["reason"] = f"Supplier has until 30th September {fy_end_year} to furnish GSTR-3B return."
        else:
            rule_37a_applicable = True
            rule_37a_reversal_amount = tot_tax
            rule_37a_trigger_evidence["reversal_amount"] = rule_37a_reversal_amount
            rule_37a_trigger_evidence["reason"] = (
                f"Supplier failed to furnish GSTR-3B by statutory cut-off 30th September {fy_end_year}. "
                f"Recipient reversal mandatory on or before 30th November {fy_end_year}."
            )

    # --- Conflict Detection & Classification ---
    review_required = (rule_37_applicable is None) or (rule_37a_applicable is None)
    both_applicable = (rule_37_applicable is True) and (rule_37a_applicable is True)
    duplicate_reversal_risk = both_applicable

    if both_applicable:
        classification_status = "BOTH_APPLICABLE_DUPLICATE_RISK"
        single_reversal_limit = max(rule_37_reversal_amount, rule_37a_reversal_amount)
    elif rule_37_applicable is True:
        classification_status = "RULE_37_ONLY"
        single_reversal_limit = rule_37_reversal_amount
    elif rule_37a_applicable is True:
        classification_status = "RULE_37A_ONLY"
        single_reversal_limit = rule_37a_reversal_amount
    elif review_required:
        classification_status = "REVIEW_REQUIRED_FACTS_PENDING"
        single_reversal_limit = Decimal("0.00")
    else:
        classification_status = "NO_REVERSAL_REQUIRED"
        single_reversal_limit = Decimal("0.00")

    return {
        "classification_status": classification_status,
        "rule_37_applicable": rule_37_applicable,
        "rule_37_reversal_amount": rule_37_reversal_amount,
        "rule_37_reversal_period": rule_37_reversal_period,
        "rule_37_trigger_evidence": rule_37_trigger_evidence,
        "rule_37a_applicable": rule_37a_applicable,
        "rule_37a_reversal_amount": rule_37a_reversal_amount,
        "rule_37a_reversal_deadline": recipient_reversal_deadline,
        "rule_37a_trigger_evidence": rule_37a_trigger_evidence,
        "both_applicable": both_applicable,
        "duplicate_reversal_risk": duplicate_reversal_risk,
        "single_reversal_limit": single_reversal_limit,
        "review_required": review_required
    }

