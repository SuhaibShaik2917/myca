import React, { useState, useEffect, useCallback } from "react";
import {
  ShieldAlert,
  AlertOctagon,
  Download,
  FileText,
  Clock,
  CheckCircle2,
  X,
  ChevronRight,
  AlertCircle,
  RefreshCw,
} from "lucide-react";
import {
  loadSession,
  saveSession,
  getTable4Summary,
  getUnifiedFinancialReport,
  listExceptions,
  getItcLedgerSummary,
  exportReconciliationAuditReport,
  Table4Summary,
  ComponentTaxAmount,
  UnifiedFinancialReportOut,
  ReconciliationExceptionOut,
  ItcLedgerSummaryOut,
  GstRegistration,
} from "../api";

interface DrillItem {
  docNo: string;
  docDate: string;
  supplierGstin: string;
  supplierName: string;
  ruleCode: string;
  tax: string;
  period: string;
  notes?: string;
}

interface Table4DrillDown {
  sectionCode: string;
  title: string;
  items: DrillItem[];
}

interface Props {
  registrations?: GstRegistration[];
}

function fmtMoney(val: unknown): string {
  const n = Number(val ?? 0);
  if (!Number.isFinite(n)) return "0.00";
  return n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function rowTotalTax(row?: ComponentTaxAmount | null): number {
  if (!row) return 0;
  return (
    Number(row.cgst || 0) +
    Number(row.sgst || 0) +
    Number(row.igst || 0) +
    Number(row.cess || 0)
  );
}

export const ItcRiskDashboard: React.FC<Props> = ({ registrations = [] }) => {
  const stored = loadSession();
  const [registrationId, setRegistrationId] = useState(
    stored.registrationId || registrations[0]?.id || ""
  );
  const [snapshotId, setSnapshotId] = useState(stored.snapshotId || "");
  const [returnPeriod, setReturnPeriod] = useState(stored.returnPeriod || "2026-11");

  const [liveSummary, setLiveSummary] = useState<Table4Summary | null>(null);
  const [unifiedReport, setUnifiedReport] = useState<UnifiedFinancialReportOut | null>(null);
  const [exceptions, setExceptions] = useState<ReconciliationExceptionOut[]>([]);
  const [ledgerSummary, setLedgerSummary] = useState<ItcLedgerSummaryOut | null>(null);
  const [liveError, setLiveError] = useState<string | null>(null);
  const [isLoadingLive, setIsLoadingLive] = useState(false);
  const [drillDownModal, setDrillDownModal] = useState<Table4DrillDown | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [selectedExceptionCategory, setSelectedExceptionCategory] = useState<string>("ALL");

  const loadLiveComplianceData = useCallback(
    async (regId: string, period: string, snapId: string) => {
      if (!regId) return;
      setIsLoadingLive(true);
      setLiveError(null);
      try {
        const [unifiedRes, excRes, ledgerRes] = await Promise.allSettled([
          getUnifiedFinancialReport(regId, period, snapId || undefined),
          listExceptions(regId, period, snapId || undefined),
          getItcLedgerSummary(period),
        ]);

        if (unifiedRes.status === "fulfilled") {
          setUnifiedReport(unifiedRes.value);
        } else {
          setUnifiedReport(null);
        }
        if (excRes.status === "fulfilled") {
          setExceptions(excRes.value);
        } else {
          setExceptions([]);
        }
        if (ledgerRes.status === "fulfilled") {
          setLedgerSummary(ledgerRes.value);
        } else {
          setLedgerSummary(null);
        }

        if (snapId) {
          const t4 = await getTable4Summary(regId, period, snapId);
          setLiveSummary(t4);
        } else {
          setLiveSummary(null);
        }
      } catch (err: any) {
        setLiveSummary(null);
        setLiveError(err?.message || "Failed to load live Table 4 statutory risk summary.");
      } finally {
        setIsLoadingLive(false);
      }
    },
    []
  );

  useEffect(() => {
    const sess = loadSession();
    const targetReg = registrationId || sess.registrationId || registrations[0]?.id || "";
    const targetSnap = snapshotId || sess.snapshotId || "";
    if (targetReg && targetReg !== registrationId) setRegistrationId(targetReg);
    if (targetSnap && targetSnap !== snapshotId) setSnapshotId(targetSnap);
    if (targetReg) {
      loadLiveComplianceData(targetReg, returnPeriod, targetSnap);
    }
  }, [registrationId, snapshotId, returnPeriod, registrations, loadLiveComplianceData]);

  const handleExportAuditReport = async () => {
    if (!registrationId || !snapshotId) {
      setLiveError("Select a Registration ID and GSTR-2B Snapshot ID before exporting the backend audit report.");
      return;
    }
    setIsExporting(true);
    setLiveError(null);
    try {
      const payload = await exportReconciliationAuditReport(registrationId, returnPeriod, snapshotId);
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `ITC_Reconciliation_Audit_Report_${returnPeriod}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      setLiveError(err?.message || "Failed to export audit report from backend.");
    } finally {
      setIsExporting(false);
    }
  };

  const comp4a = liveSummary?.components_4a;
  const comp4b = liveSummary?.components_4b;
  const comp4d = liveSummary?.components_4d;

  const t4a1 = comp4a?.["4A1_impg"];
  const t4a2 = comp4a?.["4A2_imps"];
  const t4a3 = comp4a?.["4A3_rcm"];
  const t4a4 = comp4a?.["4A4_isd"];
  const t4a5 = comp4a?.["4A5_all_other"];
  const t4b1 = comp4b?.["4B1_permanent"];
  const t4b2 = comp4b?.["4B2_total"];
  const t4c = liveSummary?.components_4c_net;
  const t4d1 = comp4d?.["4D1_reclaimed"];
  const t4d2 = comp4d?.["4D2_ineligible"];

  const grossItc = Number(liveSummary?.total_itc_available ?? unifiedReport?.table_4a_gross ?? 0);
  const netItc = Number(liveSummary?.net_itc_available ?? unifiedReport?.table_4c_net_eligible_itc ?? 0);
  const blocked4b1 = Number(
    liveSummary?.itc_reversed_rule_38_42_43 ?? unifiedReport?.table_4b1_permanent_reversals ?? 0
  );
  const tempReversed4b2 = Number(
    liveSummary?.total_itc_reversed_reclaimable_4b2 ?? unifiedReport?.table_4b2_reclaimable_reversals ?? 0
  );

  const openSectionDrillDown = (
    sectionCode: string,
    title: string,
    filterFn: (lin: Record<string, any>) => boolean
  ) => {
    const lineage = [
      ...(liveSummary?.reversals_lineage || []),
      ...(unifiedReport?.reversals_lineage || []),
    ];
    const items: DrillItem[] = lineage.filter(filterFn).map((lin) => ({
      docNo: String(lin.document_number || lin.purchase_doc_id || "DOC"),
      docDate: String(lin.document_date || "-"),
      supplierGstin: String(lin.supplier_gstin || "-"),
      supplierName: String(lin.supplier_name || "Supplier"),
      ruleCode: String(lin.rule_code || lin.table4_bucket || sectionCode),
      tax: fmtMoney(lin.total_tax ?? lin.reversal_amount ?? 0),
      period: String(lin.return_period || returnPeriod),
      notes: lin.reason ? String(lin.reason) : undefined,
    }));
    setDrillDownModal({ sectionCode, title, items });
  };

  const rule37Statement = unifiedReport?.electronic_credit_reversal_reclaimed_statement;
  const dualInterest = unifiedReport?.rule_37_interest_dual_assessment;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 flex flex-col xl:flex-row xl:items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
            <ShieldAlert className="w-5 h-5 text-indigo-600" />
            Statutory ITC Risk &amp; Form GSTR-3B Table 4 Working Dashboard
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            Live database evaluation of Section 16(2)(b) receipt gates, Section 17(5) blocked credits, Rule 37 / 37A reversals, and Notification 38/2023 Electronic Credit Reversal Statement.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {registrations.length > 0 && (
            <select
              value={registrationId}
              onChange={(e) => {
                setRegistrationId(e.target.value);
                saveSession({ registrationId: e.target.value });
              }}
              className="px-3 py-2 border border-slate-300 rounded-lg text-xs font-mono bg-white"
            >
              {registrations.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.gstin} ({r.state_code})
                </option>
              ))}
            </select>
          )}
          <input
            type="text"
            placeholder="Snapshot ID (optional)"
            value={snapshotId}
            onChange={(e) => {
              setSnapshotId(e.target.value);
              saveSession({ snapshotId: e.target.value });
            }}
            className="px-3 py-2 border border-slate-300 rounded-lg text-xs font-mono w-40"
          />
          <input
            type="month"
            value={returnPeriod}
            onChange={(e) => {
              setReturnPeriod(e.target.value);
              saveSession({ returnPeriod: e.target.value });
            }}
            className="px-3 py-2 border border-slate-300 rounded-lg text-xs font-mono"
          />
          <button
            onClick={() => loadLiveComplianceData(registrationId, returnPeriod, snapshotId)}
            disabled={isLoadingLive}
            className="px-4 py-2 bg-slate-900 hover:bg-slate-800 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoadingLive ? "animate-spin" : ""}`} />
            {isLoadingLive ? "Loading..." : "Refresh Live"}
          </button>
          <button
            onClick={handleExportAuditReport}
            disabled={isExporting || !snapshotId}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5"
          >
            <Download className="w-3.5 h-3.5" />
            {isExporting ? "Exporting..." : "Export Audit JSON"}
          </button>
        </div>
      </div>

      {liveError && (
        <div className="bg-rose-50 border border-rose-200 rounded-xl p-3 text-xs text-rose-800 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{liveError}</span>
        </div>
      )}

      {/* Top KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
          <span className="text-xs font-semibold text-slate-500 uppercase">Gross Table 4(A) ITC</span>
          <div className="text-2xl font-bold text-slate-900 mt-2 font-mono">₹{fmtMoney(grossItc)}</div>
          <p className="text-[11px] text-slate-400 mt-1">Sum of 4(A)(1) to 4(A)(5)</p>
        </div>
        <div className="bg-white rounded-xl border border-emerald-200 p-5 shadow-sm">
          <span className="text-xs font-semibold text-emerald-700 uppercase">Net Eligible Table 4(C)</span>
          <div className="text-2xl font-bold text-emerald-700 mt-2 font-mono">₹{fmtMoney(netItc)}</div>
          <p className="text-[11px] text-slate-500 mt-1">Computed net eligible ITC pending human review</p>
        </div>
        <div className="bg-white rounded-xl border border-purple-200 p-5 shadow-sm">
          <span className="text-xs font-semibold text-purple-700 uppercase">Permanent 4(B)(1) Reversal</span>
          <div className="text-2xl font-bold text-purple-700 mt-2 font-mono">₹{fmtMoney(blocked4b1)}</div>
          <p className="text-[11px] text-slate-500 mt-1">Section 17(5) &amp; Rules 38/42/43</p>
        </div>
        <div className="bg-white rounded-xl border border-amber-200 p-5 shadow-sm">
          <span className="text-xs font-semibold text-amber-700 uppercase">Reclaimable 4(B)(2) Pool</span>
          <div className="text-2xl font-bold text-amber-700 mt-2 font-mono">₹{fmtMoney(tempReversed4b2)}</div>
          <p className="text-[11px] text-slate-500 mt-1">Rule 37 / 37A / Sec 16(2)(b) Receipt Hold</p>
        </div>
      </div>

      {/* Form GSTR-3B Table 4 Working Summary Table */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 space-y-4">
        <div className="flex items-center justify-between pb-3 border-b border-slate-100">
          <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
            <FileText className="w-4 h-4 text-indigo-600" />
            Form GSTR-3B Table 4 Working Summary ({returnPeriod})
          </h3>
          {!snapshotId && (
            <span className="text-xs text-amber-700 bg-amber-50 px-2.5 py-1 rounded border border-amber-200">
              Upload GSTR-2B to load Table 4 rows
            </span>
          )}
        </div>

        <div className="overflow-x-auto border border-slate-200 rounded-lg">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-slate-50 text-slate-600 font-sans font-semibold border-b border-slate-200">
              <tr>
                <th className="px-4 py-2.5">Table 4 Details</th>
                <th className="px-4 py-2.5 text-right">IGST (₹)</th>
                <th className="px-4 py-2.5 text-right">CGST (₹)</th>
                <th className="px-4 py-2.5 text-right">SGST (₹)</th>
                <th className="px-4 py-2.5 text-right">CESS (₹)</th>
                <th className="px-4 py-2.5 text-right">Total (₹)</th>
                <th className="px-4 py-2.5 text-center">Audit</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {[
                { code: "4A1", label: "(A)(1) Import of goods", row: t4a1, scalar: liveSummary?.itc_available_import_goods, filter: "IMPORT_GOODS" },
                { code: "4A2", label: "(A)(2) Import of services (RCM)", row: t4a2, scalar: liveSummary?.itc_available_import_services, filter: "IMPORT_SERVICES" },
                { code: "4A3", label: "(A)(3) Inward supplies liable to reverse charge (RCM)", row: t4a3, scalar: liveSummary?.itc_available_inward_reverse_charge, filter: "RCM" },
                { code: "4A4", label: "(A)(4) Inward supplies from ISD", row: t4a4, scalar: liveSummary?.itc_available_isd, filter: "ISD" },
                { code: "4A5", label: "(A)(5) All other ITC", row: t4a5, scalar: liveSummary?.itc_available_all_other, filter: "4A5" },
                { code: "4B1", label: "(B)(1) Permanently Reversed ITC (Rules 38, 42, 43 & Sec 17(5))", row: t4b1, scalar: liveSummary?.itc_reversed_rule_38_42_43, filter: "4B1" },
                { code: "4B2", label: "(B)(2) Temporary Reversals (Rule 37, 37A & Sec 16(2)(b))", row: t4b2, scalar: liveSummary?.total_itc_reversed_reclaimable_4b2, filter: "4B2" },
              ].map((item) => {
                const tot = item.row ? rowTotalTax(item.row) : Number(item.scalar || 0);
                return (
                  <tr
                    key={item.code}
                    onClick={() =>
                      openSectionDrillDown(
                        item.code,
                        item.label,
                        (lin) => {
                          const bucket = String(lin.table4_bucket || "");
                          const ruleCode = String(lin.rule_code || "");
                          if (bucket) {
                            return bucket.includes(item.filter);
                          }
                          if (item.filter === "4B1") {
                            return (
                              ruleCode.startsWith("SEC_17_5") ||
                              ["RULE_38", "RULE_42", "RULE_43"].includes(ruleCode)
                            );
                          }
                          if (item.filter === "4B2") {
                            return (
                              ruleCode.startsWith("RULE_37") ||
                              ruleCode.startsWith("SEC_16_2_B") ||
                              ruleCode.includes("PENDING_RECEIPT")
                            );
                          }
                          return ruleCode.includes(item.filter);
                        }
                      )
                    }
                    className="hover:bg-slate-50 cursor-pointer"
                  >
                    <td className="px-4 py-2 font-sans font-medium text-slate-800">{item.label}</td>
                    <td className="px-4 py-2 text-right">{fmtMoney(item.row?.igst)}</td>
                    <td className="px-4 py-2 text-right">{fmtMoney(item.row?.cgst)}</td>
                    <td className="px-4 py-2 text-right">{fmtMoney(item.row?.sgst)}</td>
                    <td className="px-4 py-2 text-right">{fmtMoney(item.row?.cess)}</td>
                    <td className="px-4 py-2 text-right font-bold">{fmtMoney(tot)}</td>
                    <td className="px-4 py-2 text-center text-indigo-600">
                      <ChevronRight className="w-4 h-4 mx-auto" />
                    </td>
                  </tr>
                );
              })}
              <tr className="bg-emerald-50 font-bold text-emerald-900">
                <td className="px-4 py-2.5 font-sans">(C) Net ITC Available [(A) - (B)]</td>
                <td className="px-4 py-2.5 text-right">{fmtMoney(t4c?.igst)}</td>
                <td className="px-4 py-2.5 text-right">{fmtMoney(t4c?.cgst)}</td>
                <td className="px-4 py-2.5 text-right">{fmtMoney(t4c?.sgst)}</td>
                <td className="px-4 py-2.5 text-right">{fmtMoney(t4c?.cess)}</td>
                <td className="px-4 py-2.5 text-right">
                  {fmtMoney(t4c ? rowTotalTax(t4c) : liveSummary?.net_itc_available)}
                </td>
                <td />
              </tr>
              <tr
                onClick={() =>
                  openSectionDrillDown(
                    "4D1",
                    "(D)(1) ITC Reclaimed under Rule 37 / 37A / Sec 16(2)(b)",
                    (lin) => String(lin.table4_bucket || "").includes("4D1")
                  )
                }
                className="hover:bg-slate-50 cursor-pointer"
              >
                <td className="px-4 py-2 font-sans">(D)(1) ITC reclaimed from prior Table 4(B)(2)</td>
                <td className="px-4 py-2 text-right">{fmtMoney(t4d1?.igst)}</td>
                <td className="px-4 py-2 text-right">{fmtMoney(t4d1?.cgst)}</td>
                <td className="px-4 py-2 text-right">{fmtMoney(t4d1?.sgst)}</td>
                <td className="px-4 py-2 text-right">{fmtMoney(t4d1?.cess)}</td>
                <td className="px-4 py-2 text-right font-bold">
                  {fmtMoney(t4d1 ? rowTotalTax(t4d1) : liveSummary?.itc_reclaimed_earlier_reversal_4d1)}
                </td>
                <td className="px-4 py-2 text-center text-indigo-600">
                  <ChevronRight className="w-4 h-4 mx-auto" />
                </td>
              </tr>
              <tr
                onClick={() =>
                  openSectionDrillDown(
                    "4D2",
                    "(D)(2) Ineligible ITC (Sec 16(4) / Place of Supply)",
                    (lin) => String(lin.table4_bucket || "").includes("4D2")
                  )
                }
                className="hover:bg-slate-50 cursor-pointer"
              >
                <td className="px-4 py-2 font-sans">(D)(2) Ineligible ITC (Sec 16(4) / PoS)</td>
                <td className="px-4 py-2 text-right">{fmtMoney(t4d2?.igst)}</td>
                <td className="px-4 py-2 text-right">{fmtMoney(t4d2?.cgst)}</td>
                <td className="px-4 py-2 text-right">{fmtMoney(t4d2?.sgst)}</td>
                <td className="px-4 py-2 text-right">{fmtMoney(t4d2?.cess)}</td>
                <td className="px-4 py-2 text-right font-bold">
                  {fmtMoney(
                    t4d2
                      ? rowTotalTax(t4d2)
                      : liveSummary?.ineligible_sec_16_4_and_pos_restrictions_4d2
                  )}
                </td>
                <td className="px-4 py-2 text-center text-indigo-600">
                  <ChevronRight className="w-4 h-4 mx-auto" />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Electronic Credit Reversal Statement & Rule 37 Interest Dual Assessment */}
      {(rule37Statement || dualInterest || ledgerSummary) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-3 text-xs">
            <h3 className="font-bold text-slate-900 flex items-center gap-1.5">
              <CheckCircle2 className="w-4 h-4 text-emerald-600" />
              Electronic Credit Reversal &amp; Re-claimed Statement (Notification 38/2023)
            </h3>
            <div className="grid grid-cols-2 gap-3 font-mono">
              <div className="p-3 bg-slate-50 rounded border border-slate-100">
                <span className="font-sans text-slate-500 block">Opening Reversal Balance</span>
                <span className="font-bold text-slate-900">
                  ₹{fmtMoney(rule37Statement?.opening_reversal_balance ?? 0)}
                </span>
              </div>
              <div className="p-3 bg-slate-50 rounded border border-slate-100">
                <span className="font-sans text-slate-500 block">Period 4(B)(2) Reversed</span>
                <span className="font-bold text-amber-700">
                  ₹{fmtMoney(rule37Statement?.current_period_reversals_table_4b2 ?? tempReversed4b2)}
                </span>
              </div>
              <div className="p-3 bg-slate-50 rounded border border-slate-100">
                <span className="font-sans text-slate-500 block">Period 4(D)(1) Reclaimed</span>
                <span className="font-bold text-emerald-700">
                  ₹{fmtMoney(rule37Statement?.current_period_reclaims_table_4d1 ?? 0)}
                </span>
              </div>
              <div className="p-3 bg-indigo-50 rounded border border-indigo-100">
                <span className="font-sans text-indigo-700 block">Closing Reversal Balance / Net Active ITC</span>
                <span className="font-bold text-indigo-900">
                  ₹{fmtMoney(rule37Statement?.closing_reversal_balance ?? ledgerSummary?.net_active_itc_balance?.total ?? 0)}
                </span>
              </div>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-3 text-xs">
            <h3 className="font-bold text-slate-900 flex items-center gap-1.5">
              <Clock className="w-4 h-4 text-rose-600" />
              Rule 37 Interest Dual Assessment (Section 50(3) / Rule 88B)
            </h3>
            <div className="space-y-2">
              <div className="p-3 bg-slate-50 rounded border border-slate-100 flex justify-between">
                <span>Primary Basis (Rule 88B(3) Utilised Only):</span>
                <span className="font-mono font-bold text-emerald-700">
                  ₹{fmtMoney(dualInterest?.primary_statutory_basis_rule_88b_3?.interest_liability ?? 0)}
                </span>
              </div>
              <div className="p-3 bg-slate-50 rounded border border-slate-100 flex justify-between">
                <span>Departmental View (Sec 50(1) from Day 181):</span>
                <span className="font-mono font-bold text-rose-700">
                  ₹{fmtMoney(dualInterest?.departmental_assessment_view_sec_50_1?.basis_from_181st_day?.interest_exposure ?? 0)}
                </span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Live Exception Management Table */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-3 border-b border-slate-100 gap-2">
          <div>
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <AlertOctagon className="w-4 h-4 text-rose-600" />
              Live Statutory Exception Queue
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Exceptions queried directly from the PostgreSQL compliance &amp; reconciliation database.
            </p>
          </div>
          <span className="text-xs font-mono bg-rose-50 text-rose-700 border border-rose-200 px-2.5 py-1 rounded font-semibold">
            {exceptions.length} Live Exceptions
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <button
            onClick={() => setSelectedExceptionCategory("ALL")}
            className={`px-2.5 py-1 rounded-full font-medium ${
              selectedExceptionCategory === "ALL"
                ? "bg-slate-800 text-white"
                : "bg-slate-100 text-slate-700 hover:bg-slate-200"
            }`}
          >
            All ({exceptions.length})
          </button>
          {Array.from(new Set(exceptions.map((e) => e.category))).map((cat) => (
            <button
              key={cat}
              onClick={() => setSelectedExceptionCategory(cat)}
              className={`px-2.5 py-1 rounded-full font-mono text-[11px] ${
                selectedExceptionCategory === cat
                  ? "bg-indigo-600 text-white font-bold"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200"
              }`}
            >
              {cat.replace(/_/g, " ")} ({exceptions.filter((e) => e.category === cat).length})
            </button>
          ))}
        </div>

        <div className="border border-slate-200 rounded-lg overflow-hidden text-xs">
          <table className="w-full text-left">
            <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold font-sans">
              <tr>
                <th className="px-3 py-2.5">Category</th>
                <th className="px-3 py-2.5">Document &amp; Supplier</th>
                <th className="px-3 py-2.5 text-right font-mono">Total Tax (₹)</th>
                <th className="px-3 py-2.5">Statutory Rule</th>
                <th className="px-3 py-2.5">Suggested Remediation</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {exceptions.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-slate-500">
                    No active statutory exceptions detected in backend tables for this registration and period.
                  </td>
                </tr>
              ) : (
                exceptions
                  .filter(
                    (e) =>
                      selectedExceptionCategory === "ALL" ||
                      e.category === selectedExceptionCategory
                  )
                  .map((exc) => (
                    <tr key={exc.exception_id} className="hover:bg-slate-50/70">
                      <td className="px-3 py-2.5 font-mono font-bold text-[11px] text-slate-900">
                        {exc.category}
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="font-mono font-bold text-slate-900">{exc.document_number}</div>
                        <div className="text-[11px] text-slate-700">{exc.supplier_name || "Supplier"}</div>
                        <div className="font-mono text-[10px] text-slate-400">{exc.supplier_gstin || "-"}</div>
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono font-bold text-slate-900">
                        ₹{fmtMoney(exc.total_tax)}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="font-semibold text-indigo-700 text-[11px] block">
                          {exc.statutory_citation}
                        </span>
                        <span className="text-[11px] text-slate-600 block mt-0.5">
                          {exc.actionable_explanation}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-[11px] text-slate-800">{exc.suggested_action}</td>
                    </tr>
                  ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Drill-Down Modal */}
      {drillDownModal && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-xl shadow-2xl border border-slate-200 max-w-4xl w-full p-6 space-y-4 max-h-[85vh] flex flex-col">
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
              <div>
                <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 bg-indigo-50 text-indigo-700 rounded border border-indigo-200">
                  {drillDownModal.sectionCode}
                </span>
                <h3 className="text-base font-bold text-slate-900 mt-1">{drillDownModal.title}</h3>
              </div>
              <button
                onClick={() => setDrillDownModal(null)}
                className="p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="overflow-y-auto flex-1 border border-slate-200 rounded-lg">
              <table className="w-full text-left text-xs font-mono">
                <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 font-sans font-semibold">
                  <tr>
                    <th className="px-3 py-2.5">Document No &amp; Date</th>
                    <th className="px-3 py-2.5">Supplier</th>
                    <th className="px-3 py-2.5 text-right">Tax Amount (₹)</th>
                    <th className="px-3 py-2.5">Statutory Provenance</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {drillDownModal.items.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-4 py-8 text-center font-sans text-slate-500">
                        No document lineage rows recorded under {drillDownModal.sectionCode}.
                      </td>
                    </tr>
                  ) : (
                    drillDownModal.items.map((it, idx) => (
                      <tr key={idx} className="hover:bg-slate-50">
                        <td className="px-3 py-2.5">
                          <div className="font-bold text-slate-900">{it.docNo}</div>
                          <div className="text-[10px] text-slate-500 font-sans">{it.docDate}</div>
                        </td>
                        <td className="px-3 py-2.5 font-sans">
                          <div className="font-medium text-slate-900">{it.supplierName}</div>
                          <div className="text-[10px] text-slate-500 font-mono">{it.supplierGstin}</div>
                        </td>
                        <td className="px-3 py-2.5 text-right font-bold">₹{it.tax}</td>
                        <td className="px-3 py-2.5 font-sans">
                          <div className="font-semibold text-indigo-700 text-[10px]">{it.ruleCode}</div>
                          {it.notes && <div className="text-[10px] text-slate-500">{it.notes}</div>}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <div className="flex items-center justify-end pt-2 border-t border-slate-100">
              <button
                onClick={() => setDrillDownModal(null)}
                className="px-4 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-medium"
              >
                Close Audit View
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ItcRiskDashboard;
