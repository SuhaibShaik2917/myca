import React, { useState, useEffect, useCallback } from "react";
import {
  GitCompare,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Play,
  FileSearch,
  AlertCircle,
  BookPlus,
} from "lucide-react";
import {
  loadSession,
  saveSession,
  runReconciliation,
  resolveVariance,
  listPurchaseDocuments,
  listGstr2bDocuments,
  listUnbookedCandidates,
  bookUnbookedCandidate,
  sumGstr2bDocTax,
  PurchaseDocumentApi,
  Gstr2bDocumentOut,
  UnbookedCandidateOut,
  ReconciliationSummaryOut,
  GstRegistration,
} from "../api";

interface ReconRow {
  key: string;
  purchaseDocId?: string;
  gstr2bDocId?: string;
  bookDocNo: string;
  bookSupplierGstin: string;
  bookTaxable: number;
  bookTax: number;
  portalDocNo: string;
  portalSupplierGstin: string;
  portalTaxable: number;
  portalTax: number;
  category: string;
  matchPass: number;
  matchScore: number;
  taxDiff: number;
  isExact: boolean;
}

interface Props {
  registrations?: GstRegistration[];
}

export const ReconciliationWorkbench: React.FC<Props> = ({ registrations = [] }) => {
  const session = loadSession();
  const [registrationId, setRegistrationId] = useState(
    session.registrationId || registrations[0]?.id || ""
  );
  const [snapshotId, setSnapshotId] = useState(session.snapshotId || "");
  const [returnPeriod, setReturnPeriod] = useState(session.returnPeriod || "2026-11");
  const [activeTab, setActiveTab] = useState<
    "ALL" | "MATCHED" | "VARIANCES" | "UNBOOKED_PORTAL"
  >("ALL");
  const [selectedMatchForModal, setSelectedMatchForModal] = useState<ReconRow | null>(null);
  const [resolutionAction, setResolutionAction] = useState("FORCE_MATCH");
  const [resolutionNotes, setResolutionNotes] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runSummaryMsg, setRunSummaryMsg] = useState<string | null>(null);

  const [rows, setRows] = useState<ReconRow[]>([]);
  const [unbookedCandidates, setUnbookedCandidates] = useState<UnbookedCandidateOut[]>([]);
  const [auditTrail, setAuditTrail] = useState<
    Array<{ id: string; document: string; action: string; actor: string; timestamp: string; notes: string }>
  >([]);

  const hydrateFromBackend = useCallback(
    async (regId: string, snapId: string, period: string, runOut?: ReconciliationSummaryOut) => {
      if (!regId) return;
      try {
        const [purchaseDocs, portalDocs, unbooked] = await Promise.all([
          listPurchaseDocuments(regId, period),
          snapId ? listGstr2bDocuments(snapId) : Promise.resolve<Gstr2bDocumentOut[]>([]),
          snapId ? listUnbookedCandidates(snapId) : Promise.resolve<UnbookedCandidateOut[]>([]),
        ]);

        setUnbookedCandidates(unbooked);
        const pMap = new Map(purchaseDocs.map((p: PurchaseDocumentApi) => [p.id, p]));
        const gMap = new Map(portalDocs.map((g: Gstr2bDocumentOut) => [g.id, g]));

        if (runOut) {
          const built: ReconRow[] = [];
          for (const m of runOut.matches) {
            const p = pMap.get(m.purchase_doc_id);
            const g = gMap.get(m.gstr2b_doc_id);
            const pTax =
              Number(p?.cgst_amount || 0) +
              Number(p?.sgst_amount || 0) +
              Number(p?.igst_amount || 0) +
              Number(p?.cess_amount || 0);
            const gTax = sumGstr2bDocTax(g);
            built.push({
              key: `match-${m.purchase_doc_id}-${m.gstr2b_doc_id}`,
              purchaseDocId: m.purchase_doc_id,
              gstr2bDocId: m.gstr2b_doc_id,
              bookDocNo: p?.document_number || m.purchase_doc_id.slice(0, 8),
              bookSupplierGstin: p?.supplier_gstin || "-",
              bookTaxable: Number(p?.total_taxable_amount || 0),
              bookTax: pTax,
              portalDocNo: g?.document_number || m.gstr2b_doc_id.slice(0, 8),
              portalSupplierGstin: g?.supplier_gstin || "-",
              portalTaxable: Number(g?.taxable_value || 0),
              portalTax: gTax,
              category: m.is_exact ? "MATCHED" : "TOLERANCE_MATCH",
              matchPass: m.match_pass,
              matchScore: Number(m.match_score || 0),
              taxDiff: Number(m.tax_difference || 0),
              isExact: m.is_exact,
            });
          }
          for (const v of runOut.variances) {
            const p = v.purchase_doc_id ? pMap.get(v.purchase_doc_id) : undefined;
            const g = v.gstr2b_doc_id ? gMap.get(v.gstr2b_doc_id) : undefined;
            const pTax =
              Number(p?.cgst_amount || 0) +
              Number(p?.sgst_amount || 0) +
              Number(p?.igst_amount || 0) +
              Number(p?.cess_amount || 0);
            const gTax = sumGstr2bDocTax(g);
            built.push({
              key: `var-${v.variance_category}-${v.purchase_doc_id || "none"}-${v.gstr2b_doc_id || "none"}`,
              purchaseDocId: v.purchase_doc_id || undefined,
              gstr2bDocId: v.gstr2b_doc_id || undefined,
              bookDocNo: p?.document_number || "MISSING",
              bookSupplierGstin: p?.supplier_gstin || "-",
              bookTaxable: Number(p?.total_taxable_amount || 0),
              bookTax: pTax,
              portalDocNo: g?.document_number || "MISSING",
              portalSupplierGstin: g?.supplier_gstin || "-",
              portalTaxable: Number(g?.taxable_value || 0),
              portalTax: gTax,
              category: v.variance_category,
              matchPass: 0,
              matchScore: 0,
              taxDiff: pTax - gTax,
              isExact: false,
            });
          }
          setRows(built);
        } else {
          // Preview loaded books if reconciliation has not been triggered yet this session
          const preview: ReconRow[] = purchaseDocs.map((p) => ({
            key: `book-${p.id}`,
            purchaseDocId: p.id,
            bookDocNo: p.document_number,
            bookSupplierGstin: p.supplier_gstin || "-",
            bookTaxable: Number(p.total_taxable_amount || 0),
            bookTax:
              Number(p.cgst_amount || 0) +
              Number(p.sgst_amount || 0) +
              Number(p.igst_amount || 0) +
              Number(p.cess_amount || 0),
            portalDocNo: "Run Reconciliation",
            portalSupplierGstin: "-",
            portalTaxable: 0,
            portalTax: 0,
            category: "PENDING_RUN",
            matchPass: 0,
            matchScore: 0,
            taxDiff: 0,
            isExact: false,
          }));
          setRows(preview);
        }
      } catch (err: any) {
        setRunError(err?.message || "Failed to load reconciliation state.");
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
      hydrateFromBackend(targetReg, targetSnap, returnPeriod);
    }
  }, [registrationId, snapshotId, returnPeriod, registrations, hydrateFromBackend]);

  const handleRunLiveReconciliation = async () => {
    setIsRunning(true);
    setRunError(null);
    setRunSummaryMsg(null);
    try {
      if (!registrationId || !snapshotId) {
        throw new Error(
          "Set Registration ID and Snapshot ID (upload a GSTR-2B file first) to run live 4-Pass reconciliation."
        );
      }
      saveSession({ registrationId, snapshotId, returnPeriod });
      const res = await runReconciliation({
        registration_id: registrationId,
        return_period: returnPeriod,
        snapshot_id: snapshotId,
      });
      await hydrateFromBackend(registrationId, snapshotId, returnPeriod, res);
      setRunSummaryMsg(
        `4-Pass Run ${res.run_id.slice(0, 8)} complete: ${res.matched_count} matched, ${res.variance_count} variances, ${res.missing_in_gstr2b_count} missing in 2B, ${res.missing_in_books_count} missing in books.`
      );
    } catch (err: any) {
      setRunError(err?.message || "Reconciliation run failed.");
    } finally {
      setIsRunning(false);
    }
  };

  const handleSaveResolution = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedMatchForModal || !resolutionNotes.trim()) return;
    setRunError(null);
    try {
      const res = await resolveVariance({
        purchase_doc_id: selectedMatchForModal.purchaseDocId,
        gstr2b_doc_id: selectedMatchForModal.gstr2bDocId,
        resolution_action: resolutionAction,
        resolution_notes: resolutionNotes.trim(),
      });
      setRows((prev) =>
        prev.map((r) =>
          r.key === selectedMatchForModal.key ? { ...r, category: resolutionAction } : r
        )
      );
      setAuditTrail((prev) => [
        {
          id: res.id,
          document:
            selectedMatchForModal.bookDocNo !== "MISSING"
              ? selectedMatchForModal.bookDocNo
              : selectedMatchForModal.portalDocNo,
          action: resolutionAction,
          actor: res.resolved_by,
          timestamp: res.created_at,
          notes: resolutionNotes.trim(),
        },
        ...prev,
      ]);
      setSelectedMatchForModal(null);
      setResolutionNotes("");
      setRunSummaryMsg(`Human resolution (${resolutionAction}) persisted to backend audit log.`);
    } catch (err: any) {
      setRunError(err?.message || "Failed to persist resolution decision.");
    }
  };

  const handleBookCandidate = async (candidateId: string) => {
    if (!registrationId) return;
    setRunError(null);
    try {
      await bookUnbookedCandidate(candidateId, {
        registration_id: registrationId,
        financial_period: returnPeriod,
        notes: "Booked directly from Reconciliation Workbench",
      });
      setRunSummaryMsg("Unbooked GSTR-2B candidate promoted to Purchase Register draft.");
      await hydrateFromBackend(registrationId, snapshotId, returnPeriod);
    } catch (err: any) {
      setRunError(err?.message || "Failed to book GSTR-2B candidate.");
    }
  };

  const getStatusBadge = (cat: string) => {
    switch (cat) {
      case "MATCHED":
      case "EXACT_MATCH":
      case "FORCE_MATCH":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 text-[10px] font-semibold">
            <CheckCircle2 className="w-3 h-3" /> {cat.replace(/_/g, " ")}
          </span>
        );
      case "TOLERANCE_MATCH":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-teal-100 text-teal-800 text-[10px] font-semibold">
            Tolerance Match
          </span>
        );
      case "TAX_MISMATCH":
      case "DATE_MISMATCH":
      case "COUNTERPARTY_MISMATCH":
      case "AMENDED_BY_SUPPLIER":
      case "POSSIBLE_MATCH_REQUIRES_REVIEW":
      case "IMPORT_OR_ISD_MISMATCH":
      case "RCM_MISMATCH":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-amber-100 text-amber-800 text-[10px] font-semibold">
            <AlertTriangle className="w-3 h-3" /> {cat.replace(/_/g, " ")}
          </span>
        );
      case "MISSING_IN_GSTR2B":
      case "MISSING_IN_BOOKS":
      case "DUPLICATE_DOCUMENT":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-rose-100 text-rose-800 text-[10px] font-semibold">
            <XCircle className="w-3 h-3" /> {cat.replace(/_/g, " ")}
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-slate-100 text-slate-700 text-[10px] font-semibold">
            {cat.replace(/_/g, " ")}
          </span>
        );
    }
  };

  const filteredRows = rows.filter((r) => {
    if (activeTab === "MATCHED") {
      return r.category === "MATCHED" || r.category === "EXACT_MATCH" || r.category === "TOLERANCE_MATCH" || r.category === "FORCE_MATCH";
    }
    if (activeTab === "VARIANCES") {
      return (
        r.category === "TAX_MISMATCH" ||
        r.category === "DATE_MISMATCH" ||
        r.category === "COUNTERPARTY_MISMATCH" ||
        r.category === "AMENDED_BY_SUPPLIER" ||
        r.category === "DUPLICATE_DOCUMENT" ||
        r.category === "POSSIBLE_MATCH_REQUIRES_REVIEW" ||
        r.category === "IMPORT_OR_ISD_MISMATCH" ||
        r.category === "RCM_MISMATCH" ||
        r.category === "MISSING_IN_GSTR2B" ||
        r.category === "MISSING_IN_BOOKS"
      );
    }
    return true;
  });

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
            <GitCompare className="w-5 h-5 text-indigo-600" />
            GSTR-2B vs Purchase Books — Reconciliation Workbench
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            4-Pass Statutory Matching Engine with side-by-side comparison, unbooked candidate booking, and persisted CA human resolutions.
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
              className="px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-mono bg-white"
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
            placeholder="GSTR-2B Snapshot ID"
            value={snapshotId}
            onChange={(e) => {
              setSnapshotId(e.target.value);
              saveSession({ snapshotId: e.target.value });
            }}
            className="px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-mono w-44"
          />
          <input
            type="month"
            value={returnPeriod}
            onChange={(e) => {
              setReturnPeriod(e.target.value);
              saveSession({ returnPeriod: e.target.value });
            }}
            className="px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-mono"
          />
          <button
            onClick={handleRunLiveReconciliation}
            disabled={isRunning}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-medium tracking-wide shadow-sm flex items-center gap-2 transition-colors"
          >
            <Play className="w-4 h-4 fill-current" />
            {isRunning ? "Running Engine..." : "Run 4-Pass Engine"}
          </button>
        </div>
      </div>

      {runError && (
        <div className="bg-rose-50 border border-rose-200 rounded-xl p-3 text-xs text-rose-800 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{runError}</span>
        </div>
      )}
      {runSummaryMsg && (
        <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-3 text-xs text-emerald-800 flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{runSummaryMsg}</span>
        </div>
      )}

      {/* Filter Tabs */}
      <div className="flex flex-wrap gap-2 border-b border-slate-200 pb-3">
        {[
          { key: "ALL", label: `All Records (${rows.length})` },
          { key: "MATCHED", label: "Matched" },
          { key: "VARIANCES", label: "Variances" },
          { key: "UNBOOKED_PORTAL", label: `Unbooked GSTR-2B (${unbookedCandidates.length})` },
        ].map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key as any)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              activeTab === tab.key
                ? "bg-slate-900 text-white"
                : "bg-white text-slate-600 border border-slate-200 hover:bg-slate-50"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Main Content */}
      {activeTab === "UNBOOKED_PORTAL" ? (
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          <div className="p-4 border-b border-slate-100">
            <h3 className="text-sm font-bold text-slate-800">
              Unbooked GSTR-2B Candidates Missing in Purchase Register
            </h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
                  <th className="px-4 py-3">Document</th>
                  <th className="px-4 py-3">Supplier GSTIN</th>
                  <th className="px-4 py-3 text-right">Taxable (₹)</th>
                  <th className="px-4 py-3 text-right">Total Tax (₹)</th>
                  <th className="px-4 py-3 text-center">Receipt State</th>
                  <th className="px-4 py-3 text-center">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 font-mono">
                {unbookedCandidates.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center font-sans text-slate-500">
                      No unbooked GSTR-2B candidates in the active snapshot.
                    </td>
                  </tr>
                ) : (
                  unbookedCandidates.map((cand) => (
                    <tr key={cand.id} className="hover:bg-slate-50">
                      <td className="px-4 py-3">
                        <div className="font-bold text-slate-900">{cand.document_number}</div>
                        <div className="text-[11px] font-sans text-slate-500">{cand.document_date}</div>
                      </td>
                      <td className="px-4 py-3">{cand.supplier_gstin}</td>
                      <td className="px-4 py-3 text-right">
                        {Number(cand.taxable_value || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                      </td>
                      <td className="px-4 py-3 text-right font-bold">
                        {Number(cand.total_tax || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                      </td>
                      <td className="px-4 py-3 text-center font-sans">{cand.receipt_status}</td>
                      <td className="px-4 py-3 text-center font-sans">
                        {cand.linked_purchase_doc_id ? (
                          <span className="text-emerald-700 font-semibold">Booked</span>
                        ) : (
                          <button
                            onClick={() => handleBookCandidate(cand.id)}
                            className="px-2.5 py-1 bg-indigo-600 hover:bg-indigo-700 text-white rounded text-[10px] font-semibold inline-flex items-center gap-1"
                          >
                            <BookPlus className="w-3 h-3" /> Book to Purchase Register
                          </button>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
                  <th className="px-4 py-3 bg-slate-100/50">Book Doc No (Purchase)</th>
                  <th className="px-4 py-3 bg-slate-100/50 text-right">Book Tax (₹)</th>
                  <th className="px-4 py-3 bg-indigo-50/30">Portal Doc No (GSTR-2B)</th>
                  <th className="px-4 py-3 bg-indigo-50/30 text-right">Portal Tax (₹)</th>
                  <th className="px-4 py-3 text-center">Match Category</th>
                  <th className="px-4 py-3 text-center">Pass</th>
                  <th className="px-4 py-3 text-right">Variance (₹)</th>
                  <th className="px-4 py-3 text-center">CA Review</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 font-mono">
                {filteredRows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-10 text-center font-sans text-slate-500">
                      No reconciliation rows loaded. Select your GSTIN &amp; GSTR-2B Snapshot ID, then click &ldquo;Run 4-Pass Engine&rdquo;.
                    </td>
                  </tr>
                ) : (
                  filteredRows.map((row) => (
                    <tr key={row.key} className="hover:bg-slate-50/80 transition-colors">
                      <td className="px-4 py-3 bg-slate-50/40">
                        <div className="font-bold text-slate-900">{row.bookDocNo}</div>
                        <div className="text-[10px] text-slate-500">{row.bookSupplierGstin}</div>
                      </td>
                      <td className="px-4 py-3 bg-slate-50/40 text-right font-semibold">
                        {row.bookTax.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                      </td>
                      <td className="px-4 py-3 bg-indigo-50/10">
                        <div className="font-bold text-slate-900">{row.portalDocNo}</div>
                        <div className="text-[10px] text-slate-500">{row.portalSupplierGstin}</div>
                      </td>
                      <td className="px-4 py-3 bg-indigo-50/10 text-right font-semibold text-indigo-700">
                        {row.portalTax.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                      </td>
                      <td className="px-4 py-3 text-center font-sans">{getStatusBadge(row.category)}</td>
                      <td className="px-4 py-3 text-center">
                        <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-700 text-[10px] font-semibold">
                          {row.matchPass > 0 ? `Pass ${row.matchPass}` : "Unmatched"}
                        </span>
                      </td>
                      <td
                        className={`px-4 py-3 text-right font-bold ${
                          Math.abs(row.taxDiff) > 0.01 ? "text-rose-600" : "text-slate-400"
                        }`}
                      >
                        {row.taxDiff.toFixed(2)}
                      </td>
                      <td className="px-4 py-3 text-center font-sans">
                        <button
                          onClick={() => setSelectedMatchForModal(row)}
                          className="px-2.5 py-1 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200 rounded text-[10px] font-semibold"
                        >
                          Resolve
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Session Audit Log */}
      {auditTrail.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2 mb-3">
            <FileSearch className="w-4 h-4 text-slate-600" />
            Persisted CA Resolutions This Session
          </h3>
          <div className="space-y-2 text-xs">
            {auditTrail.map((entry) => (
              <div
                key={entry.id}
                className="flex flex-col sm:flex-row sm:items-center justify-between p-3 bg-slate-50 rounded-lg border border-slate-100"
              >
                <div className="flex items-center gap-3">
                  <span className="font-mono font-bold text-slate-900">{entry.document}</span>
                  <span className="px-2 py-0.5 bg-indigo-100 text-indigo-800 rounded text-[10px] font-semibold font-mono">
                    {entry.action}
                  </span>
                  <span className="text-slate-600">{entry.notes}</span>
                </div>
                <div className="text-[11px] text-slate-400 font-mono mt-1 sm:mt-0">
                  {entry.actor.slice(0, 8)} • {entry.timestamp}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Resolution Modal */}
      {selectedMatchForModal && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-xl shadow-xl border border-slate-200 max-w-md w-full p-6 space-y-4">
            <h3 className="text-sm font-bold text-slate-900">
              Human CA Override &amp; Resolution ({selectedMatchForModal.bookDocNo})
            </h3>
            <p className="text-xs text-slate-500">
              Your decision and audit rationale will be recorded immutably in the backend compliance ledger.
            </p>
            <form onSubmit={handleSaveResolution} className="space-y-3 text-xs">
              <div>
                <label className="block font-semibold text-slate-700 mb-1">Resolution Action</label>
                <select
                  value={resolutionAction}
                  onChange={(e) => setResolutionAction(e.target.value)}
                  className="w-full px-3 py-2 border border-slate-300 rounded font-medium"
                >
                  <option value="FORCE_MATCH">FORCE_MATCH (Accept Fuzzy Match)</option>
                  <option value="FORCE_UNLINK">FORCE_UNLINK (Break Incorrect Match)</option>
                  <option value="ACCEPT_BOOK_FIGURE">ACCEPT_BOOK_FIGURE</option>
                  <option value="ACCEPT_PORTAL_FIGURE">ACCEPT_PORTAL_FIGURE</option>
                  <option value="MARK_BLOCKED_CREDIT">MARK_BLOCKED_CREDIT (Sec 17(5) Permanent)</option>
                  <option value="DEFER_TO_NEXT_PERIOD">DEFER_TO_NEXT_PERIOD (Reclaimable)</option>
                  <option value="REQUEST_SUPPLIER_AMENDMENT">REQUEST_SUPPLIER_AMENDMENT</option>
                </select>
              </div>
              <div>
                <label className="block font-semibold text-slate-700 mb-1">Audit Trail Notes &amp; Justification</label>
                <textarea
                  rows={3}
                  value={resolutionNotes}
                  onChange={(e) => setResolutionNotes(e.target.value)}
                  placeholder="Provide statutory justification or reference..."
                  className="w-full px-3 py-2 border border-slate-300 rounded focus:ring-2 focus:ring-indigo-500"
                  required
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setSelectedMatchForModal(null)}
                  className="px-3 py-1.5 border border-slate-300 rounded text-slate-700 text-xs"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded text-xs font-medium"
                >
                  Persist Decision &amp; Log Audit
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default ReconciliationWorkbench;
