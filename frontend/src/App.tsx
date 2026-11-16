import React, { useState } from "react";
import { 
  FileText, 
  TrendingUp, 
  Award, 
  Bot, 
  ShieldCheck, 
  AlertCircle,
  Building2
} from "lucide-react";

export default function App() {
  const [activeTab, setActiveTab] = useState<"invoices" | "credit" | "schemes" | "ai">("invoices");

  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="bg-indigo-600 text-white p-2 rounded-lg font-bold text-lg">MyCA</div>
          <div>
            <h1 className="text-xl font-bold text-slate-800">Financial Companion for Indian MSMEs</h1>
            <p className="text-xs text-slate-500">Autonomous Invoicing • Credit Readiness • Scheme Matching</p>
          </div>
        </div>
        <div className="flex items-center space-x-4">
          <div className="flex items-center text-xs bg-emerald-50 text-emerald-700 px-3 py-1.5 rounded-full border border-emerald-200">
            <ShieldCheck className="w-4 h-4 mr-1.5 text-emerald-600" />
            PostgreSQL RLS Secured
          </div>
          <div className="text-right">
            <div className="text-sm font-semibold text-slate-700">Acme Tools Pvt Ltd</div>
            <div className="text-xs text-slate-400">GSTIN: 27AABCU9603R1ZM</div>
          </div>
        </div>
      </header>

      {/* Navigation */}
      <nav className="bg-white border-b border-slate-200 px-6 flex space-x-8">
        <button
          onClick={() => setActiveTab("invoices")}
          className={`py-4 px-2 font-medium text-sm flex items-center border-b-2 transition-colors ${
            activeTab === "invoices"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <FileText className="w-4 h-4 mr-2" />
          Invoice Brain
        </button>
        <button
          onClick={() => setActiveTab("credit")}
          className={`py-4 px-2 font-medium text-sm flex items-center border-b-2 transition-colors ${
            activeTab === "credit"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <TrendingUp className="w-4 h-4 mr-2" />
          Credit Builder
        </button>
        <button
          onClick={() => setActiveTab("schemes")}
          className={`py-4 px-2 font-medium text-sm flex items-center border-b-2 transition-colors ${
            activeTab === "schemes"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <Award className="w-4 h-4 mr-2" />
          Scheme Navigator
        </button>
        <button
          onClick={() => setActiveTab("ai")}
          className={`py-4 px-2 font-medium text-sm flex items-center border-b-2 transition-colors ${
            activeTab === "ai"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <Bot className="w-4 h-4 mr-2" />
          AI Financial Advisor
        </button>
      </nav>

      {/* Main Content Area */}
      <main className="flex-1 p-8 max-w-7xl mx-auto w-full">
        {activeTab === "invoices" && (
          <div className="space-y-6">
            <div className="flex justify-between items-center">
              <div>
                <h2 className="text-2xl font-bold text-slate-800">Invoice Brain</h2>
                <p className="text-sm text-slate-500">Autonomous OCR Ingestion, Validation, and GST Cross-Checking</p>
              </div>
              <button className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition">
                + Upload Invoices
              </button>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
              <div className="text-sm text-slate-600 font-medium mb-4">Pipeline Status Overview</div>
              <div className="grid grid-cols-4 gap-4">
                <div className="bg-slate-50 p-4 rounded-lg border border-slate-100">
                  <div className="text-xs text-slate-400">Uploaded</div>
                  <div className="text-2xl font-bold text-slate-800">14</div>
                </div>
                <div className="bg-blue-50 p-4 rounded-lg border border-blue-100">
                  <div className="text-xs text-blue-500">Processing OCR</div>
                  <div className="text-2xl font-bold text-blue-700">3</div>
                </div>
                <div className="bg-amber-50 p-4 rounded-lg border border-amber-100">
                  <div className="text-xs text-amber-500">Pending Review</div>
                  <div className="text-2xl font-bold text-amber-700">2</div>
                </div>
                <div className="bg-emerald-50 p-4 rounded-lg border border-emerald-100">
                  <div className="text-xs text-emerald-500">Export Ready</div>
                  <div className="text-2xl font-bold text-emerald-700">42</div>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === "credit" && (
          <div className="space-y-6">
            <div>
              <h2 className="text-2xl font-bold text-slate-800">Credit Builder</h2>
              <p className="text-sm text-slate-500">Cash Flow Analytics, Financial Provenance & Credit Readiness</p>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
              <div className="text-sm text-slate-500">Current Health Index</div>
              <div className="text-4xl font-extrabold text-emerald-600 mt-2">785 / 900</div>
              <p className="text-xs text-slate-400 mt-1">Prime MSME Rating • High Working Capital Loan Eligibility</p>
            </div>
          </div>
        )}

        {activeTab === "schemes" && (
          <div className="space-y-6">
            <div>
              <h2 className="text-2xl font-bold text-slate-800">Scheme Navigator</h2>
              <p className="text-sm text-slate-500">Deterministic Matcher for Central & State Government Subsidies</p>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
              <div className="text-sm font-semibold text-slate-700 mb-2">Recommended Central Schemes</div>
              <ul className="divide-y divide-slate-100">
                <li className="py-3 flex justify-between items-center">
                  <div>
                    <div className="font-medium text-slate-800">CGTMSE Credit Guarantee</div>
                    <div className="text-xs text-slate-500">Collateral-free credit facility up to ₹5 Crore</div>
                  </div>
                  <span className="bg-indigo-50 text-indigo-700 text-xs px-2.5 py-1 rounded-full font-medium">96% Match</span>
                </li>
                <li className="py-3 flex justify-between items-center">
                  <div>
                    <div className="font-medium text-slate-800">PM Vishwakarma Scheme</div>
                    <div className="text-xs text-slate-500">Concessional interest rate at 5% with skill support</div>
                  </div>
                  <span className="bg-indigo-50 text-indigo-700 text-xs px-2.5 py-1 rounded-full font-medium">88% Match</span>
                </li>
              </ul>
            </div>
          </div>
        )}

        {activeTab === "ai" && (
          <div className="space-y-6">
            <div>
              <h2 className="text-2xl font-bold text-slate-800">AI Financial Advisor</h2>
              <p className="text-sm text-slate-500">Powered by Google Gemini with Strict Read-Only Financial Tool Boundaries</p>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm space-y-4">
              <div className="flex items-start space-x-3 bg-slate-50 p-4 rounded-lg">
                <Bot className="w-5 h-5 text-indigo-600 mt-0.5" />
                <div className="text-sm text-slate-700">
                  Namaste! I am your MyCA financial companion. I have reviewed your October GST returns and invoices. Your input tax credit reconciliation is complete with zero discrepancies. How may I assist you with credit planning or subsidy claims today?
                </div>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
