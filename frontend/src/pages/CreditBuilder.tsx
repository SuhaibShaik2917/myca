import React, { useState, useEffect } from "react";
import {
  TrendingUp,
  Upload,
  Download,
  AlertTriangle,
  CheckCircle2,
  DollarSign,
  ArrowUpRight,
  ArrowDownRight,
  RefreshCw,
  ShieldCheck,
  AlertCircle,
} from "lucide-react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import {
  uploadBankStatement,
  getCreditSummary,
  getReadinessScore,
  getLoanDossier,
  FinancialSummaryOut,
  ReadinessScoreOut,
  LoanDossierOut,
} from "../api";

const SAMPLE_CSV = `Date,Description,Type,Amount,Reference
2025-10-01,Customer Payment Acme Tech,credit,150000,NEFT991
2025-10-03,Staff Payroll Salary Oct,debit,42000,IMPS102
2025-10-05,Office Premises Rent,debit,18000,NEFT332
2025-10-08,GST Statutory Tax Challan,debit,12000,PMT06-9
2025-10-12,Cloud Infra SaaS Fee,credit,85000,UPI554
2025-10-18,Term Loan EMI Repayment,debit,15000,ACH778
2025-11-01,Enterprise Invoice Realization,credit,210000,RTGS110
2025-11-04,Vendor Hardware Supplies,debit,54000,NEFT882
2025-11-05,Staff Payroll Salary Nov,debit,42000,IMPS441
2025-11-12,Term Loan EMI Repayment,debit,15000,ACH998`;

export default function CreditBuilder() {
  const [summary, setSummary] = useState<FinancialSummaryOut | null>(null);
  const [scorecard, setScorecard] = useState<ReadinessScoreOut | null>(null);
  const [dossier, setDossier] = useState<LoanDossierOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [csvText, setCsvText] = useState("");
  const [notification, setNotification] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadAll = async () => {
    setLoading(true);
    setError(null);
    const results = await Promise.allSettled([
      getCreditSummary(),
      getReadinessScore(),
      getLoanDossier(),
    ]);
    const errors: string[] = [];

    if (results[0].status === "fulfilled") {
      setSummary(results[0].value);
    } else {
      setSummary(null);
      errors.push(`Summary: ${results[0].reason?.message || "unavailable"}`);
    }

    if (results[1].status === "fulfilled") {
      setScorecard(results[1].value);
    } else {
      setScorecard(null);
      errors.push(`Scorecard: ${results[1].reason?.message || "unavailable"}`);
    }

    if (results[2].status === "fulfilled") {
      setDossier(results[2].value);
    } else {
      setDossier(null);
      errors.push(`Dossier: ${results[2].reason?.message || "unavailable"}`);
    }

    if (errors.length > 0) {
      setError(errors.join(" | "));
    }
    setLoading(false);
  };

  useEffect(() => {
    loadAll();
  }, []);

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!csvText.trim()) return;
    setUploading(true);
    setError(null);
    setNotification(null);
    try {
      const res = await uploadBankStatement(csvText);
      setNotification(
        `Ingested ${res.transactions_imported} verified transactions (Credits: ₹${res.total_credits}, Debits: ₹${res.total_debits})`
      );
      setCsvText("");
      await loadAll();
    } catch (err: any) {
      setError(err.message || "Statement upload failed.");
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
    a.download = `MSME_Loan_Dossier_${dossier.pan}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const chartData = (summary?.monthly_trends || []).map((t) => ({
    month: t.month,
    Inflows: Number(t.inflow || 0),
    Outflows: Number(t.outflow || 0),
  }));

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-indigo-600" />
            MSME Credit Builder &amp; Readiness Analytics
          </h2>
          <p className="text-sm text-slate-500 mt-1">
            Ingest bank/UPI statements, compute deterministic cash-flow underwriting metrics, and generate bank-grade loan dossiers.
          </p>
        </div>

        <button
          onClick={loadAll}
          disabled={loading}
          className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          <span>Refresh Metrics</span>
        </button>
      </div>

      {error && (
        <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-xs text-rose-700 flex items-start gap-2">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}
      {notification && (
        <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-xl text-xs text-emerald-700 flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
          <span>{notification}</span>
        </div>
      )}

      {/* Main Scorecard Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Credit Score Card */}
        <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm flex flex-col justify-between">
          <div>
            <div className="flex justify-between items-start">
              <span className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                MSME Readiness Index
              </span>
              <span
                className={`text-xs px-2.5 py-1 rounded-full font-bold ${
                  scorecard && scorecard.score >= 750
                    ? "bg-emerald-100 text-emerald-800"
                    : scorecard && scorecard.score >= 650
                    ? "bg-indigo-100 text-indigo-800"
                    : "bg-amber-100 text-amber-800"
                }`}
              >
                {scorecard?.rating || (loading ? "LOADING" : "NO DATA")}
              </span>
            </div>
            <div className="mt-4 flex items-baseline gap-2">
              <span className="text-5xl font-black text-slate-900">
                {scorecard ? scorecard.score : "—"}
              </span>
              <span className="text-slate-400 font-semibold text-lg">/ 900</span>
            </div>
            <p className="mt-2 text-xs text-slate-500">
              Evaluated deterministically across 4 statutory and banking parameters.
            </p>
          </div>

          <div className="mt-6 pt-4 border-t border-slate-100 flex justify-between items-center text-xs">
            <span className="text-slate-600 font-medium">DSCR Proxy Benchmark:</span>
            <span className="font-bold text-slate-800">
              {scorecard ? `${scorecard.dscr_proxy}x` : "N/A"} (Target &ge; 1.5x)
            </span>
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
                  <span className="text-xs font-bold text-indigo-600">
                    {f.score} / {f.max_score}
                  </span>
                </div>
                <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden mb-2">
                  <div
                    className="bg-indigo-600 h-1.5 rounded-full"
                    style={{ width: `${Math.min(100, (f.score / Math.max(1, f.max_score)) * 100)}%` }}
                  />
                </div>
                <p className="text-[11px] text-slate-500">{f.description}</p>
              </div>
            )) || (
              <p className="text-xs text-slate-400 col-span-2">
                Authenticate and upload bank statements to calculate pillar metrics.
              </p>
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
            <div
              className={`text-2xl font-bold mt-2 ${
                parseFloat(summary.net_profit_proxy) >= 0 ? "text-emerald-600" : "text-rose-600"
              }`}
            >
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

      {/* Monthly Cash Flow Visualization Chart */}
      {chartData.length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
          <h3 className="text-sm font-bold text-slate-800 mb-4">
            Monthly Cash-Flow Trend ({chartData.length} Months Analyzed)
          </h3>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="Inflows" fill="#10b981" radius={[4, 4, 0, 0]} />
                <Bar dataKey="Outflows" fill="#f43f5e" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Statement Ingestion & Loan Dossier */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
          <h3 className="text-sm font-bold text-slate-800 mb-2 flex items-center gap-2">
            <Upload className="w-4 h-4 text-indigo-600" />
            Ingest Bank Statement (CSV)
          </h3>
          <p className="text-xs text-slate-500 mb-4">
            Paste bank/UPI statement CSV or load the sample template. Classifies transactions into Vendor, Salary, Rent, Taxes, and Debt EMI.
          </p>

          <form onSubmit={handleUpload} className="space-y-3">
            <textarea
              rows={6}
              value={csvText}
              onChange={(e) => setCsvText(e.target.value)}
              placeholder="Date,Description,Type,Amount,Reference..."
              className="w-full border border-slate-300 rounded-lg p-3 text-xs font-mono focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none"
            />
            <div className="flex justify-between items-center">
              <button
                type="button"
                onClick={() => setCsvText(SAMPLE_CSV)}
                className="text-xs text-indigo-600 hover:text-indigo-800 font-semibold"
              >
                + Load Sample Business CSV
              </button>
              <button
                type="submit"
                disabled={uploading || !csvText.trim()}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold transition shadow-sm"
              >
                {uploading ? "Ingesting..." : "Ingest & Recalculate"}
              </button>
            </div>
          </form>
        </div>

        {/* Loan Dossier Recommendations */}
        <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm flex flex-col justify-between">
          <div>
            <h3 className="text-sm font-bold text-slate-800 mb-2 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-emerald-600" />
              Bank-Grade Loan Underwriting Dossier
            </h3>
            <p className="text-xs text-slate-500 mb-4">
              Verified statutory profile &amp; recommended working capital products for lender submission.
            </p>

            {dossier ? (
              <div className="space-y-3">
                <div className="p-3 bg-slate-50 rounded-lg border border-slate-100 text-xs flex justify-between">
                  <span className="text-slate-500">Organization / PAN:</span>
                  <span className="font-bold text-slate-800">
                    {dossier.legal_name} ({dossier.pan})
                  </span>
                </div>
                <div>
                  <span className="text-xs font-semibold text-slate-600 block mb-2">
                    Eligible Credit Facilities:
                  </span>
                  <div className="flex flex-wrap gap-2">
                    {dossier.recommended_loan_products.map((prod, idx) => (
                      <span
                        key={idx}
                        className="text-xs bg-indigo-50 text-indigo-700 border border-indigo-100 px-3 py-1 rounded-full font-medium"
                      >
                        {prod}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-xs text-slate-400">
                Authenticate and ingest statements to generate an enterprise loan dossier.
              </p>
            )}
          </div>

          <div className="mt-6 pt-4 border-t border-slate-100 flex justify-between items-center">
            <div className="text-[11px] text-slate-400">
              Generated: {dossier?.generated_at || "N/A"}
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
