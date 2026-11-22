import React, { useState, useEffect } from "react";
import { 
  TrendingUp, 
  Upload, 
  ShieldCheck, 
  AlertTriangle, 
  FileText, 
  CheckCircle2, 
  DollarSign, 
  ArrowUpRight, 
  ArrowDownRight,
  Download,
  Building,
  RefreshCw
} from "lucide-react";
import { 
  uploadBankStatement, 
  getFinancialSummary, 
  getReadinessScore, 
  getLoanDossier,
  FinancialSummaryOut,
  ReadinessScoreOut,
  LoanDossierOut,
  StatementUploadResponse
} from "../api";

export function CreditBuilder() {
  const [summary, setSummary] = useState<FinancialSummaryOut | null>(null);
  const [scorecard, setScorecard] = useState<ReadinessScoreOut | null>(null);
  const [dossier, setDossier] = useState<LoanDossierOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [csvText, setCsvText] = useState("");
  const [notification, setNotification] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const sampleCsv = `Date,Description,Type,Amount,Reference
2025-08-01,Customer RTGS Collection Invoice 101,credit,550000,REF101
2025-08-05,Steel Raw Material Vendor Purchase,debit,280000,REF102
2025-08-10,Staff Salary Payroll NEFT,debit,85000,REF103
2025-08-15,Office Premises Rent Depot,debit,45000,REF104
2025-08-20,GST PMT-06 Challan Tax Payment,debit,35000,REF105
2025-08-25,Business Loan EMI HDFC Bank,debit,25000,REF106
2025-09-02,Customer UPI Inflow Invoice 102,credit,620000,REF107
2025-09-08,Raw Materials Supplier Corp,debit,310000,REF108
2025-09-10,Staff Remuneration,debit,85000,REF109
2025-09-20,GST PMT-06 Tax Payment,debit,38000,REF110
2025-10-05,Customer NEFT Settlement,credit,710000,REF111
2025-10-12,Vendor Procurement Ltd,debit,340000,REF112
2025-10-25,Business Loan EMI HDFC Bank,debit,25000,REF113`;

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [sum, sc, dos] = await Promise.allSettled([
        getFinancialSummary(),
        getReadinessScore(),
        getLoanDossier()
      ]);
      if (sum.status === "fulfilled") setSummary(sum.value);
      if (sc.status === "fulfilled") setScorecard(sc.value);
      if (dos.status === "fulfilled") setDossier(dos.value);
    } catch (err: any) {
      setError(err?.message || "Failed to load financial data.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!csvText.trim()) return;
    setUploading(true);
    setError(null);
    try {
      const res: StatementUploadResponse = await uploadBankStatement(csvText);
      setNotification(`Imported ${res.transactions_imported} transactions. Net cash flow: ₹${res.net_flow}`);
      setCsvText("");
      await loadData();
    } catch (err: any) {
      setError(err?.message || "Upload failed.");
    } finally {
      setUploading(false);
    }
  };

  const handleDownloadDossier = () => {
    if (!dossier) return;
    const blob = new Blob([JSON.stringify(dossier, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `Loan_Dossier_${dossier.legal_name.replace(/\s+/g, "_")}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-xl md:text-2xl font-bold text-slate-800 flex items-center gap-2">
            <TrendingUp className="w-6 h-6 text-indigo-600" />
            Credit Builder & Cash Flow Analytics
          </h2>
          <p className="text-sm text-slate-500">
            Deterministic MSME Financial Readiness Index & Automated Bank Statement Categorization
          </p>
        </div>
        <button
          onClick={loadData}
          disabled={loading}
          className="px-3 py-1.5 bg-white border border-slate-300 text-slate-700 hover:bg-slate-50 rounded-lg text-xs font-semibold flex items-center gap-1.5 shadow-sm"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh Metrics
        </button>
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

      {/* Top Readiness Scorecard */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm flex flex-col justify-between">
          <div>
            <div className="flex justify-between items-start">
              <span className="text-xs font-bold text-slate-500 uppercase tracking-wider">MSME Readiness Index</span>
              <span className={`text-xs px-2.5 py-1 rounded-full font-bold ${
                scorecard && scorecard.score >= 750 ? "bg-emerald-100 text-emerald-800" :
                scorecard && scorecard.score >= 650 ? "bg-indigo-100 text-indigo-800" :
                "bg-amber-100 text-amber-800"
              }`}>
                {scorecard?.rating || "CALCULATING"}
              </span>
            </div>
            <div className="mt-4 flex items-baseline gap-2">
              <span className="text-5xl font-black text-slate-900">{scorecard?.score || 680}</span>
              <span className="text-slate-400 font-semibold text-lg">/ 900</span>
            </div>
            <p className="mt-2 text-xs text-slate-500">
              Evaluated deterministically across 4 statutory and banking parameters.
            </p>
          </div>

          <div className="mt-6 pt-4 border-t border-slate-100 flex justify-between items-center text-xs">
            <span className="text-slate-600 font-medium">DSCR Proxy Benchmark:</span>
            <span className="font-bold text-slate-800">{scorecard?.dscr_proxy || "2.10"}x (Target &ge; 1.5x)</span>
          </div>
        </div>

        {/* 4 Pillars Breakdown */}
        <div className="lg:col-span-2 bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
          <h3 className="text-sm font-bold text-slate-800 mb-4 flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-indigo-600" />
            Underwriting Pillar Analysis
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {scorecard?.factors?.map((f, idx) => (
              <div key={idx} className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <div className="flex justify-between items-center mb-1">
                  <span className="text-xs font-bold text-slate-700">{f.name}</span>
                  <span className="text-xs font-bold text-indigo-600">{f.score} / {f.max_score}</span>
                </div>
                <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden mb-2">
                  <div 
                    className="bg-indigo-600 h-1.5 rounded-full" 
                    style={{ width: `${(f.score / f.max_score) * 100}%` }}
                  />
                </div>
                <p className="text-[11px] text-slate-500 line-clamp-2">{f.description}</p>
              </div>
            )) || (
              <p className="text-xs text-slate-400 col-span-2">Upload bank statements to calculate pillar metrics.</p>
            )}
          </div>
        </div>
      </div>

      {/* Financial Health Summary */}
      {summary && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
            <div className="flex items-center justify-between text-xs text-slate-500 font-medium">
              <span>Total Revenue Inflows</span>
              <ArrowUpRight className="w-4 h-4 text-emerald-600" />
            </div>
            <div className="text-2xl font-bold text-slate-900 mt-2">₹{summary.total_revenue}</div>
          </div>
          <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
            <div className="flex items-center justify-between text-xs text-slate-500 font-medium">
              <span>Operating Outflows</span>
              <ArrowDownRight className="w-4 h-4 text-rose-600" />
            </div>
            <div className="text-2xl font-bold text-slate-900 mt-2">₹{summary.total_expenses}</div>
          </div>
          <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
            <div className="flex items-center justify-between text-xs text-slate-500 font-medium">
              <span>Net Operating Cash Flow</span>
              <DollarSign className="w-4 h-4 text-indigo-600" />
            </div>
            <div className={`text-2xl font-bold mt-2 ${parseFloat(summary.net_profit_proxy) >= 0 ? "text-emerald-600" : "text-rose-600"}`}>
              ₹{summary.net_profit_proxy}
            </div>
          </div>
          <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
            <div className="flex items-center justify-between text-xs text-slate-500 font-medium">
              <span>Net Margin Proxy</span>
              <TrendingUp className="w-4 h-4 text-indigo-600" />
            </div>
            <div className="text-2xl font-bold text-slate-900 mt-2">{summary.net_margin_pct}%</div>
          </div>
        </div>
      )}

      {/* Statement Ingestion & Loan Dossier */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Upload Statement Card */}
        <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
          <h3 className="text-sm font-bold text-slate-800 mb-2 flex items-center gap-2">
            <Upload className="w-4 h-4 text-indigo-600" />
            Ingest Bank Statement (CSV)
          </h3>
          <p className="text-xs text-slate-500 mb-4">
            Paste bank/UPI statement CSV or click to load sample data. Automatically classifies transactions into Vendor, Salary, Rent, Taxes, and Debt EMI.
          </p>

          <form onSubmit={handleUpload} className="space-y-3">
            <textarea
              rows={6}
              value={csvText}
              onChange={(e) => setCsvText(e.target.value)}
              placeholder="Date,Description,Type,Amount,Reference..."
              className="w-full p-3 text-xs font-mono border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <div className="flex justify-between items-center">
              <button
                type="button"
                onClick={() => setCsvText(sampleCsv)}
                className="text-xs text-indigo-600 hover:text-indigo-800 font-semibold"
              >
                + Insert Sample 3-Month Statement
              </button>
              <button
                type="submit"
                disabled={uploading || !csvText.trim()}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold flex items-center gap-2 transition"
              >
                <Upload className="w-3.5 h-3.5" />
                {uploading ? "Analyzing..." : "Categorize & Update Score"}
              </button>
            </div>
          </form>
        </div>

        {/* Loan Dossier Recommendations */}
        <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm flex flex-col justify-between">
          <div>
            <div className="flex justify-between items-center mb-3">
              <h3 className="text-sm font-bold text-slate-800 flex items-center gap-2">
                <Building className="w-4 h-4 text-indigo-600" />
                Bank & NBFC Loan Recommendations
              </h3>
              <span className="text-xs bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded font-medium">Pre-Approved Matches</span>
            </div>
            <p className="text-xs text-slate-500 mb-4">
              Matched based on your MSME turnover bracket, DSCR capacity, and statutory GST compliance records.
            </p>

            <ul className="space-y-2.5">
              {dossier?.recommended_loan_products?.map((prod, idx) => (
                <li key={idx} className="p-2.5 bg-slate-50 rounded-lg border border-slate-100 flex items-start gap-2.5 text-xs text-slate-700">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600 flex-shrink-0 mt-0.5" />
                  <span>{prod}</span>
                </li>
              )) || (
                <li className="text-xs text-slate-400">Loading recommendations...</li>
              )}
            </ul>
          </div>

          <div className="mt-6 pt-4 border-t border-slate-100 flex justify-between items-center">
            <div className="text-[11px] text-slate-400">
              Generated: {dossier?.generated_at || "Real-time"}
            </div>
            <button
              onClick={handleDownloadDossier}
              disabled={!dossier}
              className="px-4 py-2 bg-slate-900 hover:bg-slate-800 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold flex items-center gap-2 transition shadow-sm"
            >
              <Download className="w-3.5 h-3.5" />
              Download Loan Dossier (JSON)
            </button>
          </div>
        </div>
      </div>

      {/* Statutory Regulatory Disclaimer */}
      <div className="p-4 bg-slate-100 border border-slate-200 rounded-xl text-xs text-slate-600 flex items-start gap-3">
        <AlertTriangle className="w-5 h-5 flex-shrink-0 text-slate-500 mt-0.5" />
        <div>
          <strong className="text-slate-800">REGULATORY NOTICE:</strong> The MyCA MSME Financial Readiness Indicator is an internal, deterministic analytical metric calculated from cash-flow regularity, operating margins, and statutory GST compliance records. It is NOT an official credit bureau score (e.g., CIBIL, Equifax, Experian, CRIF High Mark) and does not constitute a loan guarantee. Final credit underwriting decisions rest exclusively with empaneled banks and financial institutions.
        </div>
      </div>
    </div>
  );
}
