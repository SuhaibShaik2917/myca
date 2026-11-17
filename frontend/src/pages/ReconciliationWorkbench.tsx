import React, { useState } from "react";
import { GitCompare, CheckCircle2, AlertCircle, ArrowRightLeft, ShieldAlert, Sparkles, Filter, ExternalLink } from "lucide-react";

export const ReconciliationWorkbench: React.FC = () => {
  const [selectedFilter, setSelectedFilter] = useState<string>("ALL");
  const [isReconciling, setIsReconciling] = useState(false);
  const [showResolveModal, setShowResolveModal] = useState<any | null>(null);
  const [resolutionAction, setResolutionAction] = useState("FORCE_MATCH");
  const [resolutionNotes, setResolutionNotes] = useState("");

  const [toleranceTaxable, setToleranceTaxable] = useState("1.00");
  const [toleranceTax, setToleranceTax] = useState("1.00");
  const [toleranceTotal, setToleranceTotal] = useState("2.00");
  const [toleranceCgst, setToleranceCgst] = useState("1.00");
  const [toleranceSgst, setToleranceSgst] = useState("1.00");
  const [toleranceIgst, setToleranceIgst] = useState("1.00");

  const [matches, setMatches] = useState([
    {
      id: "m-001",
      pass: 1,
      passName: "Pass 1: Exact Identity",
      score: "1.00",
      isExact: true,
      books: {
        docNo: "INV-2026-101",
        date: "2026-11-04",
        gstin: "27AAGCB1234F1ZQ",
        taxable: "1,00,000.00",
        tax: "18,000.00"
      },
      portal: {
        docNo: "INV-2026-101",
        date: "2026-11-04",
        gstin: "27AAGCB1234F1ZQ",
        taxable: "1,00,000.00",
        tax: "18,000.00"
      },
      difference: "0.00",
      status: "MATCHED"
    },
    {
      id: "m-002",
      pass: 2,
      passName: "Pass 2: Amendment-Aware",
      score: "0.98",
      isExact: false,
      books: {
        docNo: "AMD/9912",
        date: "2026-11-06",
        gstin: "27BBBEE5566K1Z1",
        taxable: "80,000.00",
        tax: "14,400.00"
      },
      portal: {
        docNo: "AMD/9912",
        date: "2026-11-06",
        gstin: "27BBBEE5566K1Z1",
        taxable: "80,000.00",
        tax: "14,400.00",
        originalDoc: "AMD/9910"
      },
      difference: "0.00",
      status: "AMENDED_BY_SUPPLIER"
    },
    {
      id: "m-003",
      pass: 3,
      passName: "Pass 3: Rounding Tolerance",
      score: "0.95",
      isExact: false,
      books: {
        docNo: "DEL-881",
        date: "2026-11-10",
        gstin: "07AAAAA0000A1Z5",
        taxable: "45,210.50",
        tax: "8,137.89"
      },
      portal: {
        docNo: "DEL-881",
        date: "2026-11-10",
        gstin: "07AAAAA0000A1Z5",
        taxable: "45,210.00",
        tax: "8,138.00"
      },
      difference: "0.11",
      status: "TAX_MISMATCH"
    },
    {
      id: "m-004",
      pass: 0,
      passName: "Unmatched Variance",
      score: "0.00",
      isExact: false,
      books: {
        docNo: "PUR-1049",
        date: "2026-11-14",
        gstin: "27CCCDD1122C1Z4",
        taxable: "92,000.00",
        tax: "16,560.00"
      },
      portal: null,
      difference: "16,560.00",
      status: "MISSING_IN_GSTR2B"
    }
  ]);

  const categories = [
    { code: "ALL", label: "All Items", count: 4 },
    { code: "MATCHED", label: "Exact Matched", count: 1 },
    { code: "AMENDED_BY_SUPPLIER", label: "Supplier Amended", count: 1 },
    { code: "TAX_MISMATCH", label: "Tax Discrepancy", count: 1 },
    { code: "MISSING_IN_GSTR2B", label: "Missing in 2B", count: 1 },
    { code: "MISSING_IN_BOOKS", label: "Missing in Books", count: 0 },
    { code: "DATE_MISMATCH", label: "Date Mismatch", count: 0 },
    { code: "COUNTERPARTY_MISMATCH", label: "GSTIN Mismatch", count: 0 },
    { code: "RCM_MISMATCH", label: "RCM Conflict", count: 0 },
    { code: "IMPORT_OR_ISD_MISMATCH", label: "Import/ISD", count: 0 }
  ];

  const handleRunRecon = () => {
    setIsReconciling(true);
    setTimeout(() => {
      setIsReconciling(false);
    }, 800);
  };

  const handleSaveResolution = (e: React.FormEvent) => {
    e.preventDefault();
    if (!showResolveModal) return;
    setMatches(prev => prev.map(m => m.id === showResolveModal.id ? { ...m, status: "MATCHED", passName: "Human Approved" } : m));
    setShowResolveModal(null);
    setResolutionNotes("");
  };

  const filteredMatches = selectedFilter === "ALL" 
    ? matches 
    : matches.filter(m => m.status === selectedFilter);

  return (
    <div className="space-y-6">
      {/* Top Header & Controls */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 space-y-4">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-slate-100">
          <div>
            <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
              <GitCompare className="w-5 h-5 text-indigo-600" />
              Deterministic 4-Pass Purchase Reconciliation Workbench
            </h2>
            <p className="text-xs text-slate-500 mt-1">
              Multi-pass matching engine comparing Purchase Register vs GSTR-2B Statement across 11 statutory variance categories.
            </p>
          </div>
          <button
            onClick={handleRunRecon}
            disabled={isReconciling}
            className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-medium tracking-wide shadow-sm flex items-center gap-2 transition-colors self-start md:self-auto"
          >
            <Sparkles className="w-4 h-4" />
            {isReconciling ? "Executing 4 Passes..." : "Execute 4-Pass Reconciliation"}
          </button>
        </div>

        {/* Configurable Tolerances Bar */}
        <div className="bg-slate-50 p-3 rounded-lg border border-slate-200 flex flex-wrap items-center gap-5 text-xs">
          <span className="font-semibold text-slate-700 flex items-center gap-1.5">
            <Filter className="w-3.5 h-3.5 text-slate-500" /> Tolerances:
          </span>
          <div className="flex items-center gap-1.5">
            <label className="text-slate-600">Taxable:</label>
            <input
              type="text"
              value={toleranceTaxable}
              onChange={e => setToleranceTaxable(e.target.value)}
              className="w-14 px-2 py-0.5 border border-slate-300 rounded font-mono text-center"
            />
            <span className="text-slate-400">₹</span>
          </div>
          <div className="flex items-center gap-1.5">
            <label className="text-slate-600">Total Tax:</label>
            <input
              type="text"
              value={toleranceTax}
              onChange={e => setToleranceTax(e.target.value)}
              className="w-14 px-2 py-0.5 border border-slate-300 rounded font-mono text-center"
            />
            <span className="text-slate-400">₹</span>
          </div>
          <div className="flex items-center gap-1.5">
            <label className="text-slate-600">CGST/SGST/IGST:</label>
            <input
              type="text"
              value={toleranceCgst}
              onChange={e => {
                setToleranceCgst(e.target.value);
                setToleranceSgst(e.target.value);
                setToleranceIgst(e.target.value);
              }}
              className="w-14 px-2 py-0.5 border border-slate-300 rounded font-mono text-center"
            />
            <span className="text-slate-400">₹</span>
          </div>
          <div className="flex items-center gap-1.5">
            <label className="text-slate-600">Total Value:</label>
            <input
              type="text"
              value={toleranceTotal}
              onChange={e => setToleranceTotal(e.target.value)}
              className="w-14 px-2 py-0.5 border border-slate-300 rounded font-mono text-center"
            />
            <span className="text-slate-400">₹</span>
          </div>
        </div>
              onChange={e => setToleranceTax(e.target.value)}
              className="w-16 px-2 py-1 border border-slate-300 rounded font-mono text-center"
            />
            <span className="text-slate-400">₹</span>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-slate-600">Total Invoice Value:</label>
            <input
              type="text"
              value={toleranceTotal}
              onChange={e => setToleranceTotal(e.target.value)}
              className="w-16 px-2 py-1 border border-slate-300 rounded font-mono text-center"
            />
            <span className="text-slate-400">₹</span>
          </div>
          <span className="text-[11px] text-slate-500 italic ml-auto">
            Pass 1 (Exact) → Pass 2 (Amendments) → Pass 3 (Tolerances) → Pass 4 (Controlled Near-Match)
          </span>
        </div>

        {/* Category Pills */}
        <div className="flex flex-wrap gap-2 pt-2">
          {categories.map(cat => (
            <button
              key={cat.code}
              onClick={() => setSelectedFilter(cat.code)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5 ${
                selectedFilter === cat.code
                  ? "bg-indigo-600 text-white shadow-sm"
                  : "bg-slate-100 hover:bg-slate-200 text-slate-700"
              }`}
            >
              <span>{cat.label}</span>
              <span className={`text-[10px] px-1.5 py-0.2 rounded-full ${
                selectedFilter === cat.code ? "bg-indigo-700 text-white" : "bg-slate-200 text-slate-800"
              }`}>
                {cat.count}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Side-by-Side Comparison Grid */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
          <h3 className="text-sm font-bold text-slate-800">
            Reconciliation Comparison Rows ({filteredMatches.length})
          </h3>
          <span className="text-xs text-slate-500">
            Viewing: <strong className="text-slate-800">{selectedFilter}</strong>
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
              <tr>
                <th className="px-4 py-3">Match Pass & Category</th>
                <th className="px-4 py-3">Buyer Books (Purchase Register)</th>
                <th className="px-4 py-3 text-center">
                  <ArrowRightLeft className="w-4 h-4 mx-auto text-slate-400" />
                </th>
                <th className="px-4 py-3">Supplier Filing (GSTR-2B Statement)</th>
                <th className="px-4 py-3 text-right">Tax Diff (₹)</th>
                <th className="px-4 py-3 text-center">Auditor Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filteredMatches.map(m => (
                <tr key={m.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3 align-top">
                    <span className="inline-block px-2 py-0.5 rounded text-[10px] font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200 mb-1">
                      {m.passName}
                    </span>
                    <div className="text-[11px] font-mono text-slate-600 font-medium">
                      Category: {m.status}
                    </div>
                  </td>

                  {/* Books Column */}
                  <td className="px-4 py-3 align-top font-mono">
                    {m.books ? (
                      <div>
                        <div className="font-bold text-slate-900">{m.books.docNo}</div>
                        <div className="text-[11px] text-slate-500">{m.books.date} | GSTIN: {m.books.gstin}</div>
                        <div className="text-[11px] text-slate-700 mt-1">
                          Taxable: ₹{m.books.taxable} | Tax: ₹{m.books.tax}
                        </div>
                      </div>
                    ) : (
                      <span className="text-rose-600 font-sans italic text-xs">Not recorded in books</span>
                    )}
                  </td>

                  {/* Icon Separator */}
                  <td className="px-4 py-3 align-top text-center">
                    {m.status === "MATCHED" ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-600 mx-auto" />
                    ) : (
                      <AlertCircle className="w-4 h-4 text-amber-500 mx-auto" />
                    )}
                  </td>

                  {/* Portal Column */}
                  <td className="px-4 py-3 align-top font-mono">
                    {m.portal ? (
                      <div>
                        <div className="font-bold text-slate-900">{m.portal.docNo}</div>
                        <div className="text-[11px] text-slate-500">{m.portal.date} | GSTIN: {m.portal.gstin}</div>
                        <div className="text-[11px] text-slate-700 mt-1">
                          Taxable: ₹{m.portal.taxable} | Tax: ₹{m.portal.tax}
                        </div>
                        {m.portal.originalDoc && (
                          <div className="text-[10px] text-indigo-600 mt-0.5">
                            Amends: {m.portal.originalDoc}
                          </div>
                        )}
                      </div>
                    ) : (
                      <span className="text-amber-600 font-sans italic text-xs">Missing in GSTR-2B</span>
                    )}
                  </td>

                  {/* Diff Column */}
                  <td className="px-4 py-3 align-top text-right font-mono font-bold text-slate-800">
                    ₹{m.difference}
                  </td>

                  {/* Resolution Action */}
                  <td className="px-4 py-3 align-top text-center">
                    <button
                      onClick={() => setShowResolveModal(m)}
                      className="px-2.5 py-1 bg-slate-100 hover:bg-indigo-50 hover:text-indigo-700 text-slate-700 border border-slate-300 rounded text-[11px] font-medium transition-colors"
                    >
                      Resolve Variance
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Human Resolution Modal */}
      {showResolveModal && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-xl shadow-xl border border-slate-200 max-w-lg w-full p-6 space-y-4">
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <ShieldAlert className="w-4 h-4 text-indigo-600" />
              Auditor / CA Statutory Resolution Workbench
            </h3>
            <p className="text-xs text-slate-500">
              Apply a legally auditable human resolution for document: <strong>{showResolveModal.books?.docNo || showResolveModal.portal?.docNo}</strong>.
            </p>

            <form onSubmit={handleSaveResolution} className="space-y-3 text-xs">
              <div>
                <label className="block font-semibold text-slate-700 mb-1">Resolution Action</label>
                <select
                  value={resolutionAction}
                  onChange={e => setResolutionAction(e.target.value)}
                  className="w-full px-3 py-2 border border-slate-300 rounded text-slate-800 font-medium"
                >
                  <option value="FORCE_MATCH">FORCE MATCH (Document Number Format Variant)</option>
                  <option value="ACCEPT_BOOK_FIGURE">ACCEPT BOOK FIGURE (Buyer Verified Delivery)</option>
                  <option value="ACCEPT_PORTAL_FIGURE">ACCEPT PORTAL FIGURE (Supplier Verified)</option>
                  <option value="MARK_BLOCKED_CREDIT">MARK BLOCKED CREDIT (Section 17(5))</option>
                  <option value="DEFER_TO_NEXT_PERIOD">DEFER TO NEXT PERIOD (Goods in Transit)</option>
                  <option value="REQUEST_SUPPLIER_AMENDMENT">REQUEST SUPPLIER AMENDMENT (Form GSTR-1A)</option>
                  <option value="FORCE_UNLINK">FORCE UNLINK (Spurious Match)</option>
                </select>
              </div>

              <div>
                <label className="block font-semibold text-slate-700 mb-1">Auditor Justification & Notes</label>
                <textarea
                  rows={3}
                  value={resolutionNotes}
                  onChange={e => setResolutionNotes(e.target.value)}
                  placeholder="Record statutory basis and cross-reference supporting GRN or supplier debit note..."
                  className="w-full px-3 py-2 border border-slate-300 rounded text-slate-800 font-sans"
                  required
                />
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowResolveModal(null)}
                  className="px-3 py-1.5 border border-slate-300 rounded text-slate-700 text-xs"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded text-xs font-medium"
                >
                  Record Statutory Resolution
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
