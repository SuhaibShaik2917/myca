import React, { useState, useEffect } from "react";
import { 
  FileCheck, 
  CheckCircle2, 
  AlertTriangle, 
  Send, 
  Lock, 
  Unlock, 
  RefreshCw, 
  ArrowRight,
  ShieldCheck,
  Building,
  CreditCard,
  Hash,
  ExternalLink
} from "lucide-react";
import { 
  prepareGstr3b, 
  getGstr3bStatus,
  submitGstr3bForReview,
  reviewGstr3b, 
  fileGstr3bSandbox, 
  getStoredSession,
  Gstr3bFilingResponse 
} from "../api";

export function Gstr3bFilingView() {
  const session = getStoredSession();
  const [taxPeriod, setTaxPeriod] = useState(session.returnPeriod || "2026-11");
  const [filing, setFiling] = useState<Gstr3bFilingResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [notification, setNotification] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [remarks, setRemarks] = useState("");

  const handlePrepare = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await prepareGstr3b(session.registrationId, taxPeriod);
      setFiling(res);
      setNotification(res.message || `Prepared GSTR-3B for ${taxPeriod}`);
      setTimeout(() => setNotification(null), 4000);
    } catch (err: any) {
      setError(err?.message || "Failed to prepare GSTR-3B.");
    } finally {
      setLoading(false);
    }
  };

  const handleReviewAction = async (action: "SUBMIT_FOR_REVIEW" | "APPROVE" | "REJECT") => {
    if (!filing) return;
    setActionLoading(true);
    setError(null);
    try {
      if (action === "SUBMIT_FOR_REVIEW") {
        await submitGstr3bForReview(filing.filing_id, remarks || undefined);
      } else {
        const decision = action === "APPROVE" ? "APPROVED" : "REJECTED";
        const effectiveRemarks = (remarks || "").trim().length >= 3
          ? remarks.trim()
          : decision === "APPROVED"
            ? "Verified and approved by CA Checker."
            : "Returned for correction by CA Checker.";
        await reviewGstr3b(filing.filing_id, decision, effectiveRemarks);
      }
      const refreshed = await getGstr3bStatus(session.registrationId, taxPeriod);
      setFiling(refreshed);
      setNotification(`Status updated to ${refreshed.filing_status}`);
      setRemarks("");
      setTimeout(() => setNotification(null), 4000);
    } catch (err: any) {
      setError(err?.message || `Review action ${action} failed.`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleFileSandbox = async () => {
    if (!filing) return;
    setActionLoading(true);
    setError(null);
    try {
      const res = await fileGstr3bSandbox(filing.filing_id);
      setFiling(res);
      setNotification(`GSTR-3B filed successfully! ARN: ${res.arn}`);
      setTimeout(() => setNotification(null), 5000);
    } catch (err: any) {
      setError(err?.message || "Filing failed.");
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-xl md:text-2xl font-bold text-slate-800 flex items-center gap-2">
            <FileCheck className="w-6 h-6 text-indigo-600" />
            GSTR-3B Preparation & Rule 88A Offset Engine
          </h2>
          <p className="text-sm text-slate-500">
            Statutory Electronic Credit Ledger Utilization & Maker-Checker Sandbox Filing Gate
          </p>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="text"
            value={taxPeriod}
            onChange={(e) => setTaxPeriod(e.target.value)}
            className="px-3 py-1.5 text-xs font-mono border border-slate-300 rounded-lg w-28"
            placeholder="YYYY-MM"
          />
          <button
            onClick={handlePrepare}
            disabled={loading}
            className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 shadow-sm transition"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            {loading ? "Preparing..." : "Prepare Return"}
          </button>
        </div>
      </div>

      {/* Notifications */}
      {notification && (
        <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-xl text-emerald-800 text-sm flex items-center gap-2">
          <CheckCircle2 className="w-5 h-5 flex-shrink-0 text-emerald-600" />
          <span>{notification}</span>
        </div>
      )}

      {error && (
        <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-rose-800 text-sm flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 flex-shrink-0 text-rose-600" />
          <span>{error}</span>
        </div>
      )}

      {/* ARN Banner when Filed */}
      {filing && filing.filing_status === "FILED" && (
        <div className="p-5 bg-emerald-600 text-white rounded-2xl shadow-sm space-y-2">
          <div className="flex justify-between items-start">
            <div className="flex items-center gap-2 font-bold text-base">
              <ShieldCheck className="w-5 h-5" />
              <span>GSTR-3B Successfully Filed (GSTN Sandbox)</span>
            </div>
            <span className="text-xs bg-emerald-700 px-2.5 py-1 rounded font-mono">
              Filing Date: {filing.filing_date}
            </span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs font-mono pt-2 border-t border-emerald-500/50">
            <div>
              <span className="opacity-80">Application Reference Number (ARN):</span>
              <div className="font-bold text-sm tracking-wide">{filing.arn}</div>
            </div>
            <div>
              <span className="opacity-80">Challan Identification Number (CIN):</span>
              <div className="font-bold text-sm tracking-wide">{filing.cin}</div>
            </div>
          </div>
        </div>
      )}

      {/* Maker-Checker Workflow Bar */}
      {filing && (
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
          <div className="flex items-center gap-3">
            <div className="text-xs font-bold text-slate-500 uppercase">Workflow Status:</div>
            <span className={`px-3 py-1 rounded-full text-xs font-bold ${
              filing.filing_status === "FILED" ? "bg-emerald-100 text-emerald-800" :
              filing.filing_status === "APPROVED_BY_CHECKER" ? "bg-indigo-100 text-indigo-800" :
              filing.filing_status === "SUBMITTED_FOR_REVIEW" ? "bg-amber-100 text-amber-800" :
              filing.filing_status === "REJECTED_BY_CHECKER" ? "bg-rose-100 text-rose-800" :
              "bg-slate-100 text-slate-700"
            }`}>
              {filing.filing_status}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {filing.filing_status === "DRAFT" && (
              <button
                onClick={() => handleReviewAction("SUBMIT_FOR_REVIEW")}
                disabled={actionLoading}
                className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-semibold shadow-sm"
              >
                Submit for Checker Review
              </button>
            )}

            {filing.filing_status === "SUBMITTED_FOR_REVIEW" && (
              <>
                <button
                  onClick={() => handleReviewAction("APPROVE")}
                  disabled={actionLoading}
                  className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-xs font-semibold shadow-sm"
                >
                  Approve (Checker Gate)
                </button>
                <button
                  onClick={() => handleReviewAction("REJECT")}
                  disabled={actionLoading}
                  className="px-3 py-1.5 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-xs font-semibold shadow-sm"
                >
                  Reject & Return to Draft
                </button>
              </>
            )}

            {filing.filing_status === "APPROVED_BY_CHECKER" && (
              <button
                onClick={handleFileSandbox}
                disabled={actionLoading}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-xs font-bold flex items-center gap-1.5 shadow-md transition"
              >
                <Lock className="w-3.5 h-3.5" />
                <span>Submit to GSTN Sandbox (GSP)</span>
              </button>
            )}
          </div>
        </div>
      )}

      {/* Main GSTR-3B Tables */}
      {filing && (
        <div className="space-y-6">
          {/* Table 3.1 & Table 4 Side-by-Side */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Table 3.1 Outward Supplies */}
            <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-sm font-bold text-slate-800">
                  Table 3.1: Tax on Outward Supplies
                </h3>
                <span className="text-[11px] bg-slate-100 text-slate-600 px-2 py-0.5 rounded font-mono">
                  From Approved Invoices
                </span>
              </div>

              <table className="w-full text-xs text-left">
                <thead className="bg-slate-50 border-b border-slate-200 text-slate-600">
                  <tr>
                    <th className="py-2 px-3">Nature of Supply</th>
                    <th className="py-2 px-3 text-right">Taxable (₹)</th>
                    <th className="py-2 px-3 text-right">IGST (₹)</th>
                    <th className="py-2 px-3 text-right">CGST (₹)</th>
                    <th className="py-2 px-3 text-right">SGST (₹)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 font-mono">
                  <tr>
                    <td className="py-2 px-3 font-sans font-medium text-slate-700">3.1(a) Taxable Outward</td>
                    <td className="py-2 px-3 text-right">{filing.table_3_1?.a_taxable_supplies?.taxable_value || "0.00"}</td>
                    <td className="py-2 px-3 text-right">{filing.table_3_1?.a_taxable_supplies?.igst || "0.00"}</td>
                    <td className="py-2 px-3 text-right">{filing.table_3_1?.a_taxable_supplies?.cgst || "0.00"}</td>
                    <td className="py-2 px-3 text-right">{filing.table_3_1?.a_taxable_supplies?.sgst || "0.00"}</td>
                  </tr>
                  <tr className="bg-slate-50 font-bold">
                    <td className="py-2 px-3 font-sans">Total Output Liability</td>
                    <td className="py-2 px-3 text-right">—</td>
                    <td className="py-2 px-3 text-right">{filing.table_3_1?.total_tax_payable?.igst || "0.00"}</td>
                    <td className="py-2 px-3 text-right">{filing.table_3_1?.total_tax_payable?.cgst || "0.00"}</td>
                    <td className="py-2 px-3 text-right">{filing.table_3_1?.total_tax_payable?.sgst || "0.00"}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            {/* Table 4 Eligible ITC */}
            <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-sm font-bold text-slate-800">
                  Table 4: Eligible Input Tax Credit
                </h3>
                <span className="text-[11px] bg-slate-100 text-slate-600 px-2 py-0.5 rounded font-mono">
                  From Reconciled 2B
                </span>
              </div>

              <table className="w-full text-xs text-left">
                <thead className="bg-slate-50 border-b border-slate-200 text-slate-600">
                  <tr>
                    <th className="py-2 px-3">ITC Category</th>
                    <th className="py-2 px-3 text-right">IGST (₹)</th>
                    <th className="py-2 px-3 text-right">CGST (₹)</th>
                    <th className="py-2 px-3 text-right">SGST (₹)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 font-mono">
                  <tr>
                    <td className="py-2 px-3 font-sans font-medium text-slate-700">4(A) Eligible ITC Available</td>
                    <td className="py-2 px-3 text-right">{filing.table_4?.a_itc_available?.igst || "0.00"}</td>
                    <td className="py-2 px-3 text-right">{filing.table_4?.a_itc_available?.cgst || "0.00"}</td>
                    <td className="py-2 px-3 text-right">{filing.table_4?.a_itc_available?.sgst || "0.00"}</td>
                  </tr>
                  <tr>
                    <td className="py-2 px-3 font-sans text-rose-700">4(B) ITC Reversed</td>
                    <td className="py-2 px-3 text-right text-rose-700">({filing.table_4?.b_itc_reversed?.igst || "0.00"})</td>
                    <td className="py-2 px-3 text-right text-rose-700">({filing.table_4?.b_itc_reversed?.cgst || "0.00"})</td>
                    <td className="py-2 px-3 text-right text-rose-700">({filing.table_4?.b_itc_reversed?.sgst || "0.00"})</td>
                  </tr>
                  <tr className="bg-slate-50 font-bold text-indigo-900">
                    <td className="py-2 px-3 font-sans">4(C) Net ITC Available</td>
                    <td className="py-2 px-3 text-right">{filing.table_4?.c_net_itc?.igst || "0.00"}</td>
                    <td className="py-2 px-3 text-right">{filing.table_4?.c_net_itc?.cgst || "0.00"}</td>
                    <td className="py-2 px-3 text-right">{filing.table_4?.c_net_itc?.sgst || "0.00"}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* Rule 88A Offset Engine & PMT-06 Cash Calculation */}
          <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
            <div className="flex justify-between items-center mb-4">
              <div>
                <h3 className="text-sm font-bold text-slate-800 flex items-center gap-2">
                  <CreditCard className="w-4 h-4 text-indigo-600" />
                  Statutory Rule 88A Electronic Ledger Offset & Cash Challan
                </h3>
                <p className="text-xs text-slate-500 mt-0.5">
                  Mandatory waterfall: IGST credit exhausted 100% first &rarr; CGST/SGST cross-offset strictly prohibited
                </p>
              </div>
              <div className="text-right">
                <span className="text-[11px] text-slate-400 uppercase font-semibold">Net Cash Payable (PMT-06)</span>
                <div className="text-2xl font-black text-rose-700 font-mono">
                  ₹{filing.offset_result?.total_challan_required || "0.00"}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 pt-2">
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <span className="text-[11px] font-bold text-slate-500">IGST Cash Challan</span>
                <div className="text-lg font-bold font-mono text-slate-900 mt-1">
                  ₹{filing.offset_result?.igst?.challan_required || "0.00"}
                </div>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <span className="text-[11px] font-bold text-slate-500">CGST Cash Challan</span>
                <div className="text-lg font-bold font-mono text-slate-900 mt-1">
                  ₹{filing.offset_result?.cgst?.challan_required || "0.00"}
                </div>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <span className="text-[11px] font-bold text-slate-500">SGST Cash Challan</span>
                <div className="text-lg font-bold font-mono text-slate-900 mt-1">
                  ₹{filing.offset_result?.sgst?.challan_required || "0.00"}
                </div>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <span className="text-[11px] font-bold text-slate-500">Cess Cash Challan</span>
                <div className="text-lg font-bold font-mono text-slate-900 mt-1">
                  ₹{filing.offset_result?.cess?.challan_required || "0.00"}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Empty State */}
      {!filing && (
        <div className="bg-white rounded-xl border border-slate-200 p-12 text-center shadow-sm space-y-3">
          <FileCheck className="w-12 h-12 text-slate-300 mx-auto" />
          <h3 className="text-base font-bold text-slate-700">No GSTR-3B Generated for Period {taxPeriod}</h3>
          <p className="text-xs text-slate-500 max-w-md mx-auto">
            Click &ldquo;Prepare Return&rdquo; above to automatically aggregate Table 3.1 outward liabilities, Table 4 eligible ITC, and compute statutory Rule 88A ledger offsets.
          </p>
          <button
            onClick={handlePrepare}
            disabled={loading}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-semibold shadow-sm inline-flex items-center gap-1.5"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            Prepare Return Now
          </button>
        </div>
      )}
    </div>
  );
}
