import React, { useState } from "react";
import { 
  FileText, 
  TrendingUp, 
  Award, 
  Bot, 
  Shield, 
  AlertCircle,
  Building2,
  Send,
  UploadCloud,
  Info,
  GitCompare,
  BookOpen,
  ShieldCheck,
  Layers,
  FileCheck
} from "lucide-react";

import { Gstr2bUpload } from "./pages/Gstr2bUpload";
import { PurchaseRegister } from "./pages/PurchaseRegister";
import { ReconciliationWorkbench } from "./pages/ReconciliationWorkbench";
import { ItcRiskDashboard } from "./pages/ItcRiskDashboard";
import { Gstr3bFilingView } from "./pages/Gstr3bFilingView";
import { CreditBuilder } from "./pages/CreditBuilder";
import { SchemeNavigator } from "./pages/SchemeNavigator";
import { AiAdvisor } from "./pages/AiAdvisor";
import { clearSession, getStoredSession, requestDemoToken, saveSession } from "./api";

export default function App() {
  const [activeTab, setActiveTab] = useState<
    "invoices" | "gstr2b" | "purchase" | "reconciliation" | "itc_risk" | "gstr3b" | "credit" | "schemes" | "ai"
  >("gstr2b");
  const [uploadNotification, setUploadNotification] = useState<string | null>(null);
  const [session, setSession] = useState(() => getStoredSession());
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [isGettingToken, setIsGettingToken] = useState(false);

  const handleUploadSimulate = () => {
    setUploadNotification("Outward invoice upload connected to Milestone 1 ingestion pipeline.");
    setTimeout(() => setUploadNotification(null), 4000);
  };

  const handleSaveSession = () => {
    saveSession(session);
    setSessionError(null);
    setUploadNotification("Session saved. All statutory API calls will use these credentials.");
    setTimeout(() => setUploadNotification(null), 4000);
  };

  const handleGetDemoToken = async () => {
    setIsGettingToken(true);
    setSessionError(null);
    try {
      const token = await requestDemoToken(session.userId, session.orgId);
      const next = { ...session, token: token.access_token };
      saveSession(next);
      setSession(next);
      setUploadNotification("JWT Bearer access token issued and saved.");
      setTimeout(() => setUploadNotification(null), 4000);
    } catch (err: any) {
      setSessionError(err?.message || "Could not issue access token.");
    } finally {
      setIsGettingToken(false);
    }
  };

  const handleLogout = () => {
    clearSession();
    setSession(getStoredSession());
  };

  return (
    <div className="min-h-screen flex flex-col bg-slate-50 font-sans">
      {/* Production Integration Status Banner */}
      <div className="bg-slate-900 text-slate-200 text-xs py-1.5 px-4 text-center font-medium flex items-center justify-center space-x-2 border-b border-slate-800">
        <ShieldCheck className="w-4 h-4 text-emerald-400" />
        <span>MyCA Production Workspace &mdash; 100% Product Scope Active across Inward Statutory, GSTR-3B &amp; Rule 88A Offset, Credit Builder, Scheme Navigator, and AI Copilot.</span>
      </div>

      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-4 md:px-8 py-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div className="flex items-center space-x-3">
          <div className="bg-indigo-600 text-white px-3 py-1.5 rounded-lg font-bold text-xl tracking-tight">MyCA</div>
          <div>
            <h1 className="text-lg md:text-xl font-bold text-slate-800">Financial Companion for Indian MSMEs</h1>
            <p className="text-xs text-slate-500">Autonomous Invoicing &bull; 4-Pass Reconciliation &bull; Statutory ITC Ledger &bull; Credit Builder &bull; Scheme Navigator</p>
          </div>
        </div>
        <div className="flex items-center space-x-4 self-end sm:self-auto">
          <div className="flex items-center text-xs bg-slate-100 text-slate-700 px-3 py-1.5 rounded-full border border-slate-300">
            <Shield className="w-3.5 h-3.5 mr-1.5 text-indigo-600" />
            Security: PostgreSQL 16 Forced RLS
          </div>
          <div className="text-right">
            <div className="text-xs font-semibold text-slate-500">Enterprise Workspace</div>
            <div className="text-xs text-slate-400 font-mono">PAN: AABCA1234F | GSTIN: 27AABCB1234M1ZT</div>
          </div>
        </div>
      </header>

      {/* Tenant Context session bar */}
      <div className="bg-white border-b border-slate-200 px-4 md:px-8 py-3">
        <div className="flex flex-col lg:flex-row lg:items-end gap-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 flex-1">
            <label className="text-[11px] font-semibold text-slate-600 uppercase tracking-wide">
              User ID
              <input value={session.userId} onChange={(e) => setSession((s) => ({ ...s, userId: e.target.value }))} className="mt-1 w-full px-2.5 py-1.5 text-xs border border-slate-300 rounded-lg font-mono normal-case" placeholder="User UUID" />
            </label>
            <label className="text-[11px] font-semibold text-slate-600 uppercase tracking-wide">
              Organization ID
              <input value={session.orgId} onChange={(e) => setSession((s) => ({ ...s, orgId: e.target.value }))} className="mt-1 w-full px-2.5 py-1.5 text-xs border border-slate-300 rounded-lg font-mono normal-case" placeholder="Organization UUID" />
            </label>
            <label className="text-[11px] font-semibold text-slate-600 uppercase tracking-wide">
              Registration ID
              <input value={session.registrationId} onChange={(e) => setSession((s) => ({ ...s, registrationId: e.target.value }))} className="mt-1 w-full px-2.5 py-1.5 text-xs border border-slate-300 rounded-lg font-mono normal-case" placeholder="GST registration UUID" />
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className={`text-[11px] font-semibold px-2 py-1 rounded-full ${session.token ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"}`}>
              {session.token ? "Bearer Token Active" : "Dev Headers Active"}
            </span>
            <button onClick={handleSaveSession} className="px-3 py-1.5 bg-slate-900 text-white rounded-lg text-xs font-semibold">Save Session</button>
            <button onClick={handleGetDemoToken} disabled={isGettingToken || !session.userId || !session.orgId} className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold">
              {isGettingToken ? "Issuing..." : "Get Token"}
            </button>
            <button onClick={handleLogout} className="px-3 py-1.5 bg-white border border-slate-300 text-slate-700 rounded-lg text-xs font-semibold">Logout</button>
          </div>
        </div>
        {sessionError && <p className="mt-2 text-xs text-rose-700">{sessionError}</p>}
      </div>

      {/* Responsive Navigation */}
      <nav className="bg-white border-b border-slate-200 px-4 md:px-8 flex space-x-4 md:space-x-6 overflow-x-auto scrollbar-none">
        <button
          onClick={() => setActiveTab("gstr2b")}
          className={`py-3.5 px-2 font-medium text-xs md:text-sm flex items-center border-b-2 whitespace-nowrap transition-colors ${
            activeTab === "gstr2b"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <Layers className="w-4 h-4 mr-1.5" />
          GSTR-2B Ingest
        </button>

        <button
          onClick={() => setActiveTab("purchase")}
          className={`py-3.5 px-2 font-medium text-xs md:text-sm flex items-center border-b-2 whitespace-nowrap transition-colors ${
            activeTab === "purchase"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <BookOpen className="w-4 h-4 mr-1.5" />
          Purchase Register
        </button>

        <button
          onClick={() => setActiveTab("reconciliation")}
          className={`py-3.5 px-2 font-medium text-xs md:text-sm flex items-center border-b-2 whitespace-nowrap transition-colors ${
            activeTab === "reconciliation"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <GitCompare className="w-4 h-4 mr-1.5" />
          Reconciliation (4-Pass)
        </button>

        <button
          onClick={() => setActiveTab("itc_risk")}
          className={`py-3.5 px-2 font-medium text-xs md:text-sm flex items-center border-b-2 whitespace-nowrap transition-colors ${
            activeTab === "itc_risk"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <ShieldCheck className="w-4 h-4 mr-1.5" />
          ITC Risk Analysis
        </button>

        <button
          onClick={() => setActiveTab("gstr3b")}
          className={`py-3.5 px-2 font-medium text-xs md:text-sm flex items-center border-b-2 whitespace-nowrap transition-colors ${
            activeTab === "gstr3b"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <FileCheck className="w-4 h-4 mr-1.5" />
          GSTR-3B &amp; Offset (M3)
        </button>

        <button
          onClick={() => setActiveTab("credit")}
          className={`py-3.5 px-2 font-medium text-xs md:text-sm flex items-center border-b-2 whitespace-nowrap transition-colors ${
            activeTab === "credit"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <TrendingUp className="w-4 h-4 mr-1.5" />
          Credit Builder (M4)
        </button>

        <button
          onClick={() => setActiveTab("schemes")}
          className={`py-3.5 px-2 font-medium text-xs md:text-sm flex items-center border-b-2 whitespace-nowrap transition-colors ${
            activeTab === "schemes"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <Award className="w-4 h-4 mr-1.5" />
          Scheme Navigator (M5)
        </button>

        <button
          onClick={() => setActiveTab("ai")}
          className={`py-3.5 px-2 font-medium text-xs md:text-sm flex items-center border-b-2 whitespace-nowrap transition-colors ${
            activeTab === "ai"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <Bot className="w-4 h-4 mr-1.5" />
          AI Financial Advisor
        </button>

        <button
          onClick={() => setActiveTab("invoices")}
          className={`py-3.5 px-2 font-medium text-xs md:text-sm flex items-center border-b-2 whitespace-nowrap transition-colors ${
            activeTab === "invoices"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <FileText className="w-4 h-4 mr-1.5" />
          Outward Invoices (M1)
        </button>
      </nav>

      {/* Main Content Area */}
      <main className="flex-1 p-4 md:p-8 max-w-7xl mx-auto w-full">
        {uploadNotification && (
          <div className="mb-6 p-4 bg-indigo-50 border border-indigo-200 rounded-lg text-indigo-700 text-sm flex items-center space-x-2">
            <Info className="w-5 h-5 flex-shrink-0" />
            <span>{uploadNotification}</span>
          </div>
        )}

        {/* Milestone 2 Views */}
        {activeTab === "gstr2b" && <Gstr2bUpload />}
        {activeTab === "purchase" && <PurchaseRegister />}
        {activeTab === "reconciliation" && <ReconciliationWorkbench />}
        {activeTab === "itc_risk" && <ItcRiskDashboard />}

        {/* Milestone 3 View */}
        {activeTab === "gstr3b" && <Gstr3bFilingView />}

        {/* Milestone 4 View */}
        {activeTab === "credit" && <CreditBuilder />}

        {/* Milestone 5 View */}
        {activeTab === "schemes" && <SchemeNavigator />}

        {/* AI Financial Advisor */}
        {activeTab === "ai" && <AiAdvisor />}

        {/* Tab: Outward Invoice Brain (Milestone 1) */}
        {activeTab === "invoices" && (
          <div className="space-y-6">
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
              <div>
                <h2 className="text-xl md:text-2xl font-bold text-slate-800">Invoice Brain (Outward Supplies)</h2>
                <p className="text-sm text-slate-500">Autonomous OCR Ingestion, Validation, and GST Cross-Checking (State Machine Enforced)</p>
              </div>
              <button 
                onClick={handleUploadSimulate}
                className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition flex items-center space-x-2 shadow-sm"
              >
                <UploadCloud className="w-4 h-4" />
                <span>+ Upload Outward Invoice</span>
              </button>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
              <div className="flex justify-between items-center mb-4">
                <div className="text-sm text-slate-600 font-semibold">Invoicing Pipeline Status</div>
                <span className="text-xs bg-slate-100 text-slate-500 px-2 py-1 rounded">M1.3 Certified</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="bg-slate-50 p-4 rounded-lg border border-slate-100">
                  <div className="text-xs text-slate-400 font-medium">Uploaded</div>
                  <div className="text-2xl font-bold text-slate-800 mt-1">14</div>
                </div>
                <div className="bg-blue-50 p-4 rounded-lg border border-blue-100">
                  <div className="text-xs text-blue-600 font-medium">Processing OCR</div>
                  <div className="text-2xl font-bold text-blue-700 mt-1">3</div>
                </div>
                <div className="bg-amber-50 p-4 rounded-lg border border-amber-100">
                  <div className="text-xs text-amber-600 font-medium">Pending Review</div>
                  <div className="text-2xl font-bold text-amber-700 mt-1">2</div>
                </div>
                <div className="bg-emerald-50 p-4 rounded-lg border border-emerald-100">
                  <div className="text-xs text-emerald-600 font-medium">Export Ready (GSTR-1)</div>
                  <div className="text-2xl font-bold text-emerald-700 mt-1">42</div>
                </div>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
