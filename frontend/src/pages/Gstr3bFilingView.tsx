import React, { useState, useEffect, useCallback } from "react";
import {
  FileCheck2,
  ArrowRightLeft,
  ShieldCheck,
  CheckCircle2,
  XCircle,
  Send,
  AlertTriangle,
  Sparkles,
  FileSignature,
  RefreshCw,
} from "lucide-react";
import {
  loadSession,
  saveSession,
  getGstr3bStatus,
  prepareGstr3b,
  submitGstr3bForReview,
  reviewGstr3bDecision,
  fileGstr3bToSandbox,
  Gstr3bRecord,
  GstRegistration,
} from "../api";

interface Props {
  registrations?: GstRegistration[];
}

function fmtMoney(val: unknown): string {
  const n = Number(val ?? 0);
  if (!Number.isFinite(n)) return "0.00";
  return n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export default function Gstr3bFilingView({ registrations = [] }: Props) {
  const stored = loadSession();
  const [registrationId, setRegistrationId] = useState(
    stored.registrationId || registrations[0]?.id || ""
  );
  const [taxPeriod, setTaxPeriod] = useState(stored.returnPeriod || "2026-11");
  const [filing, setFiling] = useState<Gstr3bRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [remarks, setRemarks] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notification, setNotification] = useState<string | null>(null);

  const loadExistingStatus = useCallback(async (regId: string, period: string) => {
    if (!regId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await getGstr3bStatus(regId, period);
      setFiling(res);
    } catch (err: any) {
      const msg = String(err?.message || "");
      if (msg.includes("HTTP 404")) {
        setFiling(null);
      } else {
        setError(msg || "Failed to load GSTR-3B return status.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const sess = loadSession();
    const targetReg = registrationId || sess.registrationId || registrations[0]?.id || "";
    if (targetReg && targetReg !== registrationId) {
      setRegistrationId(targetReg);
    }
    if (targetReg) {
      loadExistingStatus(targetReg, taxPeriod);
    }
  }, [registrationId, taxPeriod, registrations, loadExistingStatus]);

  const handlePrepare = async () => {
    if (!registrationId) {
      setError("Please select a GST Registration first.");
      return;
    }
    saveSession({ registrationId, returnPeriod: taxPeriod });
    setLoading(true);
    setError(null);
    setNotification(null);
    try {
      const res = await prepareGstr3b(registrationId, taxPeriod);
      setFiling(res);
      setNotification("GSTR-3B working draft prepared with Rule 88A / Section 49(4) offset computation.");
    } catch (err: any) {
      setError(err.message || "Failed to prepare GSTR-3B draft.");
    } finally {
      setLoading(false);
    }
  };

  const handleWorkflowAction = async (
    action: "submit" | "approve" | "reject" | "file"
  ) => {
    if (!filing) return;
    if ((action === "approve" || action === "reject") && !remarks.trim()) {
      setError("Reviewer remarks are required to approve or reject a GSTR-3B filing.");
      return;
    }
    const filingId = filing.id || filing.filing_id || "";
    const regId = filing.registration_id || registrationId;
    if (!filingId) return;
    setLoading(true);
    setError(null);
    setNotification(null);
    try {
      if (action === "submit") {
        await submitGstr3bForReview(filingId, remarks || "Submitted for Checker statutory verification");
        setNotification("Return submitted to Checker for verification.");
      } else if (action === "approve") {
        await reviewGstr3bDecision(filingId, "APPROVED", remarks.trim());
        setNotification("Return APPROVED by Checker. Filing Gate unlocked.");
      } else if (action === "reject") {
        await reviewGstr3bDecision(filingId, "REJECTED", remarks.trim());
        setNotification("Return REJECTED by Checker. Maker can revise and re-prepare.");
      } else if (action === "file") {
        await fileGstr3bToSandbox(filingId);
        setNotification("Return transmitted. ARN & CIN stamped.");
      }
      setRemarks("");
      await loadExistingStatus(regId, filing.tax_period);
    } catch (err: any) {
      setError(err.message || "Workflow transition failed.");
    } finally {
      setLoading(false);
    }
  };

  const effectiveStatus = filing?.filing_status || filing?.status || "DRAFT";
  const effectiveGst = filing?.gstin || registrations.find((r) => r.id === (filing?.registration_id || registrationId))?.gstin || "";
  const effectiveCheckerNotes = filing?.checker_remarks || filing?.checker_notes || null;

  const t31 = filing?.table_3_1 || filing?.table_3_1_data || {};
  const t31a = t31.a_taxable_supplies || t31.a_outward_taxable || {};
  const t31b = t31.b_zero_rated || t31.b_outward_zero_rated || {};
  const t31c = t31.c_other_outward_exempt || { taxable_value: t31.c_other_nil_exempt ?? 0 };
  const t31d = t31.d_inward_rcm || {};
  const t4 = filing?.table_4 || filing?.table_4_data || {};
  const t4a = t4.a_itc_available || t4.table_4_a_5_all_other_itc || t4.table_4_a_5 || {};
  const t4a1 = t4.table_4_a_1_import_goods || {};
  const t4a3 = t4.table_4_a_3_inward_rcm || {};
  const t4a4 = t4.table_4_a_4_isd || {};
  const t4b1 = t4.table_4_b_1_permanent_reversal || t4.table_4_b_1 || {};
  const t4b2 = t4.b_itc_reversed || t4.table_4_b_2_reclaimable_reversal || t4.table_4_b_2 || {};
  const t4c = t4.c_net_itc || t4.table_4_c_net_itc || {};
  const t4d1 = t4.table_4_d_1_reclaimed || {};
  const t4d2 = t4.d_ineligible_other || t4.table_4_d_2_ineligible || {};
  const offset = filing?.offset || filing?.offset_data || {};

  const statusColors: Record<string, string> = {
    DRAFT: "bg-slate-100 text-slate-700 border-slate-300",
    SUBMITTED_FOR_REVIEW: "bg-amber-100 text-amber-800 border-amber-300",
    REJECTED_BY_CHECKER: "bg-rose-100 text-rose-800 border-rose-300",
    APPROVED_BY_CHECKER: "bg-indigo-100 text-indigo-800 border-indigo-300",
    FILED: "bg-emerald-100 text-emerald-800 border-emerald-300",
  };

  const canPrepare = !filing || effectiveStatus === "DRAFT" || effectiveStatus === "REJECTED_BY_CHECKER";

  return (
    <div className="space-y-6">
      {/* Top Action Header */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <FileCheck2 className="w-5 h-5 text-indigo-600" />
            GSTR-3B Rule 88A Offset &amp; Maker-Checker Filing
          </h2>
          <p className="text-sm text-slate-500 mt-1">
            Automated ITC utilization, Section 49(4) RCM cash discharge, maker-checker governance, and attested GSTN filing.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {registrations.length > 0 ? (
            <select
              value={registrationId}
              onChange={(e) => {
                setRegistrationId(e.target.value);
                saveSession({ registrationId: e.target.value });
              }}
              className="border border-slate-300 rounded-lg px-3 py-1.5 text-xs font-mono bg-white"
            >
              {registrations.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.gstin} ({r.state_code})
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              placeholder="Registration UUID"
              value={registrationId}
              onChange={(e) => {
                setRegistrationId(e.target.value);
                saveSession({ registrationId: e.target.value });
              }}
              className="border border-slate-300 rounded-lg px-3 py-1.5 text-xs font-mono w-48"
            />
          )}
          <input
            type="month"
            value={taxPeriod}
            onChange={(e) => {
              setTaxPeriod(e.target.value);
              saveSession({ returnPeriod: e.target.value });
            }}
            className="border border-slate-300 rounded-lg px-3 py-1.5 text-xs font-mono"
          />
          <button
            onClick={handlePrepare}
            disabled={loading || !registrationId || !canPrepare}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold flex items-center gap-2 transition shadow-sm"
          >
            <Sparkles className="w-3.5 h-3.5" />
            <span>
              {loading
                ? "Processing..."
                : effectiveStatus === "REJECTED_BY_CHECKER"
                ? "Re-Prepare Rejected Draft"
                : "Prepare Return & Offset"}
            </span>
          </button>
          <button
            onClick={() => loadExistingStatus(registrationId, taxPeriod)}
            disabled={loading || !registrationId}
            className="px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-semibold flex items-center gap-1"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {error && (
        <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-xs text-rose-700 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}
      {notification && (
        <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-xl text-xs text-emerald-700 flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
          <span>{notification}</span>
        </div>
      )}

      {/* Workspace Card */}
      {filing ? (
        <div className="space-y-6">
          {/* Status Ribbon */}
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm flex flex-wrap items-center justify-between gap-4">
            <div className="flex flex-wrap items-center gap-4">
              <div
                className={`px-3 py-1 rounded-full text-xs font-bold border ${
                  statusColors[effectiveStatus] || "bg-slate-100 text-slate-700"
                }`}
              >
                {effectiveStatus.replace(/_/g, " ")}
              </div>
              <div className="text-xs text-slate-600 font-mono">
                GSTIN: <span className="font-bold text-slate-900">{effectiveGst}</span> • Period:{" "}
                <span className="font-bold">{filing.tax_period}</span>
              </div>
              {effectiveStatus === "FILED" && (
                <span
                  className={`px-2.5 py-1 rounded-full text-[11px] font-bold border ${
                    filing.is_live_portal_filing
                      ? "bg-emerald-50 text-emerald-800 border-emerald-200"
                      : "bg-amber-50 text-amber-800 border-amber-200"
                  }`}
                >
                  {filing.is_live_portal_filing
                    ? "LIVE PORTAL TRANSMITTED"
                    : "SANDBOX SIMULATION (Non-Statutory Test ARN)"}
                </span>
              )}
            </div>

            {filing.arn && (
              <div className="flex flex-wrap items-center gap-3 bg-emerald-50 border border-emerald-200 px-3 py-1.5 rounded-lg text-xs">
                <span className="font-semibold text-emerald-800">ARN: {filing.arn}</span>
                {filing.cin && <span className="text-emerald-600 font-mono">CIN: {filing.cin}</span>}
                {filing.gsp_reference_id && (
                  <span className="text-emerald-700 font-mono">Ref: {filing.gsp_reference_id}</span>
                )}
              </div>
            )}
          </div>

          {effectiveCheckerNotes && (
            <div className="p-3 bg-slate-50 border border-slate-200 rounded-xl text-xs text-slate-700">
              <strong>Latest Review Remarks:</strong> {effectiveCheckerNotes}
            </div>
          )}

          {/* Statutory Computation Grids */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Table 3.1 Liability (including 3.1(d) RCM) */}
            <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
              <h3 className="text-sm font-bold text-slate-800 mb-3">
                Table 3.1 — Outward &amp; Inward RCM Supplies Tax Liability
              </h3>
              <table className="w-full text-left text-xs font-mono">
                <thead className="bg-slate-50 text-slate-500 border-b border-slate-200 font-sans">
                  <tr>
                    <th className="p-2">Nature of Supplies</th>
                    <th className="p-2 text-right">Taxable (₹)</th>
                    <th className="p-2 text-right">IGST (₹)</th>
                    <th className="p-2 text-right">CGST (₹)</th>
                    <th className="p-2 text-right">SGST (₹)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  <tr>
                    <td className="p-2 font-sans font-semibold">(a) Outward taxable</td>
                    <td className="p-2 text-right">{fmtMoney(t31a.taxable_value)}</td>
                    <td className="p-2 text-right">{fmtMoney(t31a.igst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t31a.cgst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t31a.sgst)}</td>
                  </tr>
                  <tr>
                    <td className="p-2 font-sans">(b) Zero-rated / Exports</td>
                    <td className="p-2 text-right">{fmtMoney(t31b.taxable_value)}</td>
                    <td className="p-2 text-right">{fmtMoney(t31b.igst)}</td>
                    <td className="p-2 text-right">0.00</td>
                    <td className="p-2 text-right">0.00</td>
                  </tr>
                  <tr>
                    <td className="p-2 font-sans">(c) Exempt / Nil-rated</td>
                    <td className="p-2 text-right">{fmtMoney(t31c.taxable_value)}</td>
                    <td className="p-2 text-right">0.00</td>
                    <td className="p-2 text-right">0.00</td>
                    <td className="p-2 text-right">0.00</td>
                  </tr>
                  <tr className="bg-amber-50/50">
                    <td className="p-2 font-sans font-semibold text-amber-900">
                      (d) Inward RCM (Cash Only — Sec 49(4))
                    </td>
                    <td className="p-2 text-right">{fmtMoney(t31d.taxable_value)}</td>
                    <td className="p-2 text-right">{fmtMoney(t31d.igst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t31d.cgst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t31d.sgst)}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            {/* Table 4 Complete Breakdown */}
            <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
              <h3 className="text-sm font-bold text-slate-800 mb-3">
                Table 4 — Eligible ITC &amp; Reversal Summary
              </h3>
              <table className="w-full text-left text-xs font-mono">
                <thead className="bg-slate-50 text-slate-500 border-b border-slate-200 font-sans">
                  <tr>
                    <th className="p-2">Table 4 Row</th>
                    <th className="p-2 text-right">IGST (₹)</th>
                    <th className="p-2 text-right">CGST (₹)</th>
                    <th className="p-2 text-right">SGST (₹)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  <tr>
                    <td className="p-2 font-sans">4(A)(1) Import of goods</td>
                    <td className="p-2 text-right">{fmtMoney(t4a1.igst)}</td>
                    <td className="p-2 text-right">0.00</td>
                    <td className="p-2 text-right">0.00</td>
                  </tr>
                  <tr>
                    <td className="p-2 font-sans">4(A)(3) Inward RCM supplies</td>
                    <td className="p-2 text-right">{fmtMoney(t4a3.igst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t4a3.cgst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t4a3.sgst)}</td>
                  </tr>
                  <tr>
                    <td className="p-2 font-sans">4(A)(4) Inward ISD</td>
                    <td className="p-2 text-right">{fmtMoney(t4a4.igst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t4a4.cgst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t4a4.sgst)}</td>
                  </tr>
                  <tr>
                    <td className="p-2 font-sans">4(A)(5) All other ITC</td>
                    <td className="p-2 text-right">{fmtMoney(t4a.igst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t4a.cgst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t4a.sgst)}</td>
                  </tr>
                  <tr className="text-rose-700">
                    <td className="p-2 font-sans">(-) 4(B)(1) Permanent reversal (Sec 17(5)/R42)</td>
                    <td className="p-2 text-right">{fmtMoney(t4b1.igst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t4b1.cgst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t4b1.sgst)}</td>
                  </tr>
                  <tr className="text-amber-700">
                    <td className="p-2 font-sans">(-) 4(B)(2) Reclaimable (Rule 37/37A/16(2)(b))</td>
                    <td className="p-2 text-right">{fmtMoney(t4b2.igst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t4b2.cgst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t4b2.sgst)}</td>
                  </tr>
                  <tr className="bg-emerald-50/70 font-bold text-emerald-900">
                    <td className="p-2 font-sans">4(C) Net ITC Available</td>
                    <td className="p-2 text-right">{fmtMoney(t4c.igst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t4c.cgst)}</td>
                    <td className="p-2 text-right">{fmtMoney(t4c.sgst)}</td>
                  </tr>
                  <tr className="text-slate-600">
                    <td className="p-2 font-sans">4(D)(1) Reclaimed ITC | 4(D)(2) Ineligible</td>
                    <td className="p-2 text-right" colSpan={3}>
                      Reclaimed: ₹{fmtMoney((Number(t4d1.igst || 0) + Number(t4d1.cgst || 0) + Number(t4d1.sgst || 0)))} | Ineligible: ₹{fmtMoney((Number(t4d2.igst || 0) + Number(t4d2.cgst || 0) + Number(t4d2.sgst || 0)))}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* Tax Offset Ledger */}
          <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
            <div className="flex flex-wrap justify-between items-center mb-4 gap-2">
              <h3 className="text-sm font-bold text-slate-800 flex items-center gap-2">
                <ArrowRightLeft className="w-4 h-4 text-indigo-600" />
                Rule 88A Cross-Utilization Offset &amp; PMT-06 Cash Challan Ledger
              </h3>
              <span className="text-xs bg-slate-100 text-slate-600 px-2.5 py-1 rounded-full font-semibold">
                IGST Credit Exhausted First (RCM Discharged in Cash)
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 text-xs">
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <span className="text-slate-400 block mb-1">IGST Settled</span>
                <div className="font-bold text-slate-800">
                  Credit: ₹{fmtMoney(offset.igst?.paid_by_igst_credit ?? offset.paid_igst_by_igst)}
                </div>
                <div className="text-rose-600 font-semibold">
                  Cash: ₹{fmtMoney(offset.igst?.challan_required ?? offset.cash_igst_required)}
                </div>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <span className="text-slate-400 block mb-1">CGST Settled</span>
                <div className="font-bold text-slate-800">
                  Credit: ₹{fmtMoney(offset.cgst?.total_credit_used ?? (Number(offset.paid_cgst_by_igst || 0) + Number(offset.paid_cgst_by_cgst || 0)))}
                </div>
                <div className="text-rose-600 font-semibold">
                  Cash: ₹{fmtMoney(offset.cgst?.challan_required ?? offset.cash_cgst_required)}
                </div>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <span className="text-slate-400 block mb-1">SGST Settled</span>
                <div className="font-bold text-slate-800">
                  Credit: ₹{fmtMoney(offset.sgst?.total_credit_used ?? (Number(offset.paid_sgst_by_igst || 0) + Number(offset.paid_sgst_by_sgst || 0)))}
                </div>
                <div className="text-rose-600 font-semibold">
                  Cash: ₹{fmtMoney(offset.sgst?.challan_required ?? offset.cash_sgst_required)}
                </div>
              </div>
              <div className="p-3 bg-indigo-50 border border-indigo-100 rounded-lg">
                <span className="text-indigo-700 font-semibold block mb-1">Net PMT-06 Cash Challan</span>
                <div className="text-lg font-black text-indigo-900">
                  ₹{fmtMoney(offset.total_challan_required ?? offset.total_tax_paid_by_cash)}
                </div>
                <div className="text-[11px] text-indigo-600">
                  Closing Credit Carry-Forward: ₹
                  {fmtMoney(
                    offset.remaining_credit_balance?.total ??
                      (Number(offset.closing_balance_igst || 0) +
                        Number(offset.closing_balance_cgst || 0) +
                        Number(offset.closing_balance_sgst || 0))
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Maker-Checker Governance Action Panel */}
          <div className="bg-slate-900 rounded-xl p-6 text-white shadow-lg flex flex-col gap-4">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
              <div>
                <h4 className="text-sm font-bold flex items-center gap-2">
                  <ShieldCheck className="w-4 h-4 text-indigo-400" />
                  Maker-Checker Filing Gate Control
                </h4>
                <p className="text-xs text-slate-300 mt-1">
                  GST returns cannot transmit unless explicitly verified and approved by an organizational Checker (Admin/Owner).
                </p>
              </div>

              <div className="flex flex-wrap gap-2">
                {(effectiveStatus === "DRAFT" || effectiveStatus === "REJECTED_BY_CHECKER") && (
                  <button
                    onClick={() => handleWorkflowAction("submit")}
                    disabled={loading}
                    className="px-4 py-2 bg-amber-500 hover:bg-amber-600 text-slate-950 rounded-lg text-xs font-bold flex items-center gap-1.5 shadow transition"
                  >
                    <Send className="w-3.5 h-3.5" />
                    <span>
                      {effectiveStatus === "REJECTED_BY_CHECKER"
                        ? "Resubmit to Checker"
                        : "Submit for Review"}
                    </span>
                  </button>
                )}

                {effectiveStatus === "SUBMITTED_FOR_REVIEW" && (
                  <>
                    <button
                      onClick={() => handleWorkflowAction("approve")}
                      disabled={loading}
                      className="px-4 py-2 bg-emerald-500 hover:bg-emerald-600 text-slate-950 rounded-lg text-xs font-bold flex items-center gap-1.5 shadow transition"
                    >
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      <span>Approve (Checker)</span>
                    </button>
                    <button
                      onClick={() => handleWorkflowAction("reject")}
                      disabled={loading}
                      className="px-4 py-2 bg-rose-500 hover:bg-rose-600 text-white rounded-lg text-xs font-bold flex items-center gap-1.5 shadow transition"
                    >
                      <XCircle className="w-3.5 h-3.5" />
                      <span>Reject</span>
                    </button>
                  </>
                )}

                {effectiveStatus === "APPROVED_BY_CHECKER" && (
                  <button
                    onClick={() => handleWorkflowAction("file")}
                    disabled={loading}
                    className="px-5 py-2.5 bg-indigo-500 hover:bg-indigo-400 text-white rounded-lg text-xs font-bold flex items-center gap-2 shadow-lg transition"
                  >
                    <FileSignature className="w-4 h-4" />
                    <span>File GSTR-3B (Attested)</span>
                  </button>
                )}

                {effectiveStatus === "FILED" && (
                  <span className="px-4 py-2 bg-emerald-950 border border-emerald-500/50 text-emerald-300 rounded-lg text-xs font-bold flex items-center gap-2">
                    <CheckCircle2 className="w-4 h-4" />
                    Return Filed &amp; Immutable
                  </span>
                )}
              </div>
            </div>

            {effectiveStatus !== "FILED" && (
              <div className="pt-2 border-t border-slate-700/80">
                <label className="block text-[11px] font-semibold text-slate-300 mb-1">
                  Maker / Checker Audit Remarks
                </label>
                <input
                  type="text"
                  value={remarks}
                  onChange={(e) => setRemarks(e.target.value)}
                  placeholder="Enter statutory verification note or rejection reason (required for Checker decision)..."
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white placeholder:text-slate-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
                />
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-dashed border-slate-300 p-12 text-center">
          <FileCheck2 className="w-10 h-10 text-slate-300 mx-auto mb-3" />
          <h3 className="text-sm font-bold text-slate-700">No GSTR-3B Return Loaded for {taxPeriod}</h3>
          <p className="text-xs text-slate-500 mt-1 max-w-md mx-auto">
            Select an active GSTIN and return period, then click &ldquo;Prepare Return &amp; Offset&rdquo; to compute Table 3.1 liabilities, Table 4 Net ITC, and Rule 88A tax settlement.
          </p>
        </div>
      )}
    </div>
  );
}
