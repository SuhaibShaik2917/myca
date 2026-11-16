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
  Info
} from "lucide-react";

export default function App() {
  const [activeTab, setActiveTab] = useState<"invoices" | "credit" | "schemes" | "ai">("invoices");
  const [chatMessages, setChatMessages] = useState<Array<{ sender: "user" | "assistant"; text: string }>>([
    {
      sender: "assistant",
      text: "Namaste! I am your MyCA financial companion. (Demo Mode: Backend services and database migration verification in progress). How can I assist you today?"
    }
  ]);
  const [inputText, setInputText] = useState("");
  const [uploadNotification, setUploadNotification] = useState<string | null>(null);

  const handleSendMessage = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim()) return;

    const userMsg = inputText.trim();
    setChatMessages((prev) => [...prev, { sender: "user", text: userMsg }]);
    setInputText("");

    setTimeout(() => {
      setChatMessages((prev) => [
        ...prev,
        {
          sender: "assistant",
          text: `[Simulated Response] Received inquiry regarding: "${userMsg}". Note: Live Gemini API execution will be linked after full database migration baseline sign-off.`
        }
      ]);
    }, 600);
  };

  const handleUploadSimulate = () => {
    setUploadNotification("Demo Upload: In production, invoices are streamed to secure object storage and queued via public.claim_task().");
    setTimeout(() => setUploadNotification(null), 4000);
  };

  return (
    <div className="min-h-screen flex flex-col bg-slate-50 font-sans">
      {/* Demo Mode Notification Banner */}
      <div className="bg-amber-500 text-white text-xs py-1.5 px-4 text-center font-medium flex items-center justify-center space-x-2">
        <AlertCircle className="w-4 h-4" />
        <span>Demonstration Mode / Mock Preview — Backend migrations under formal security audit. Real financial data is not being processed.</span>
      </div>

      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-4 md:px-8 py-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div className="flex items-center space-x-3">
          <div className="bg-indigo-600 text-white px-3 py-1.5 rounded-lg font-bold text-xl tracking-tight">MyCA</div>
          <div>
            <h1 className="text-lg md:text-xl font-bold text-slate-800">Financial Companion for Indian MSMEs</h1>
            <p className="text-xs text-slate-500">Autonomous Invoicing • Readiness Indicators • Scheme Discovery</p>
          </div>
        </div>
        <div className="flex items-center space-x-4 self-end sm:self-auto">
          <div className="flex items-center text-xs bg-slate-100 text-slate-700 px-3 py-1.5 rounded-full border border-slate-300">
            <Shield className="w-3.5 h-3.5 mr-1.5 text-indigo-600" />
            Security Baseline: PostgreSQL 16.15 RLS
          </div>
          <div className="text-right">
            <div className="text-xs font-semibold text-slate-500">[Demo Profile] Acme Tools Pvt Ltd</div>
            <div className="text-xs text-slate-400 font-mono">GSTIN: 27AABCU9603R1ZM</div>
          </div>
        </div>
      </header>

      {/* Responsive Navigation */}
      <nav className="bg-white border-b border-slate-200 px-4 md:px-8 flex space-x-4 md:space-x-8 overflow-x-auto scrollbar-none">
        <button
          onClick={() => setActiveTab("invoices")}
          className={`py-3.5 px-2 font-medium text-sm flex items-center border-b-2 whitespace-nowrap transition-colors ${
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
          className={`py-3.5 px-2 font-medium text-sm flex items-center border-b-2 whitespace-nowrap transition-colors ${
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
          className={`py-3.5 px-2 font-medium text-sm flex items-center border-b-2 whitespace-nowrap transition-colors ${
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
          className={`py-3.5 px-2 font-medium text-sm flex items-center border-b-2 whitespace-nowrap transition-colors ${
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
      <main className="flex-1 p-4 md:p-8 max-w-7xl mx-auto w-full">
        {uploadNotification && (
          <div className="mb-6 p-4 bg-indigo-50 border border-indigo-200 rounded-lg text-indigo-700 text-sm flex items-center space-x-2">
            <Info className="w-5 h-5 flex-shrink-0" />
            <span>{uploadNotification}</span>
          </div>
        )}

        {/* Tab 1: Invoice Brain */}
        {activeTab === "invoices" && (
          <div className="space-y-6">
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
              <div>
                <h2 className="text-xl md:text-2xl font-bold text-slate-800">Invoice Brain</h2>
                <p className="text-sm text-slate-500">Autonomous OCR Ingestion, Validation, and GST Cross-Checking (State Machine Enforced)</p>
              </div>
              <button 
                onClick={handleUploadSimulate}
                className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition flex items-center space-x-2 shadow-sm"
              >
                <UploadCloud className="w-4 h-4" />
                <span>+ Upload Invoices</span>
              </button>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
              <div className="flex justify-between items-center mb-4">
                <div className="text-sm text-slate-600 font-semibold">Simulated Invoicing Pipeline</div>
                <span className="text-xs bg-slate-100 text-slate-500 px-2 py-1 rounded">Sample Data</span>
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
                  <div className="text-xs text-emerald-600 font-medium">Export Ready</div>
                  <div className="text-2xl font-bold text-emerald-700 mt-1">42</div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Tab 2: Credit Builder */}
        {activeTab === "credit" && (
          <div className="space-y-6">
            <div>
              <h2 className="text-xl md:text-2xl font-bold text-slate-800">Credit Builder</h2>
              <p className="text-sm text-slate-500">Cash Flow Analytics, Financial Provenance & Loan Readiness Profiling</p>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
              <div className="flex justify-between items-start">
                <div>
                  <div className="text-sm font-semibold text-slate-700">Internal MSME Financial Readiness Indicator</div>
                  <div className="text-4xl font-extrabold text-emerald-600 mt-2">785 <span className="text-xl font-normal text-slate-400">/ 900</span></div>
                </div>
                <span className="text-xs bg-slate-100 text-slate-500 px-2 py-1 rounded">Simulated Score</span>
              </div>
              <div className="mt-4 p-3 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-500">
                <strong className="text-slate-700">Important Disclaimer:</strong> This metric is an internal, deterministic demonstration indicator calculated from transaction stability and cash-flow regularity. It is NOT an official credit score from credit rating agencies or bureaus (e.g., CIBIL, Equifax, Experian, CRIF High Mark) and does not constitute a loan guarantee.
              </div>
            </div>
          </div>
        )}

        {/* Tab 3: Scheme Navigator */}
        {activeTab === "schemes" && (
          <div className="space-y-6">
            <div>
              <h2 className="text-xl md:text-2xl font-bold text-slate-800">Scheme Navigator</h2>
              <p className="text-sm text-slate-500">Deterministic Matcher for Central & State MSME Government Subsidies</p>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
              <div className="text-sm font-semibold text-slate-700 mb-2">Simulated Catalog Matching</div>
              <ul className="divide-y divide-slate-100">
                <li className="py-3 flex justify-between items-center">
                  <div>
                    <div className="font-medium text-slate-800">CGTMSE Credit Guarantee Scheme</div>
                    <div className="text-xs text-slate-500">Collateral-free credit facility up to ₹5 Crore</div>
                  </div>
                  <span className="bg-indigo-50 text-indigo-700 text-xs px-2.5 py-1 rounded-full font-medium">Sample Match</span>
                </li>
                <li className="py-3 flex justify-between items-center">
                  <div>
                    <div className="font-medium text-slate-800">PM Vishwakarma Scheme</div>
                    <div className="text-xs text-slate-500">Concessional interest rate at 5% with collateral assistance</div>
                  </div>
                  <span className="bg-indigo-50 text-indigo-700 text-xs px-2.5 py-1 rounded-full font-medium">Sample Match</span>
                </li>
              </ul>
            </div>
          </div>
        )}

        {/* Tab 4: AI Financial Advisor */}
        {activeTab === "ai" && (
          <div className="space-y-6">
            <div>
              <h2 className="text-xl md:text-2xl font-bold text-slate-800">AI Financial Advisor</h2>
              <p className="text-sm text-slate-500">Powered by Google Gemini with Strict Read-Only Financial Tool Boundaries</p>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm flex flex-col h-[480px]">
              <div className="flex-1 p-4 overflow-y-auto space-y-4">
                {chatMessages.map((msg, idx) => (
                  <div 
                    key={idx} 
                    className={`flex items-start space-x-3 ${msg.sender === "user" ? "flex-row-reverse space-x-reverse" : ""}`}
                  >
                    <div className={`p-2 rounded-full ${msg.sender === "user" ? "bg-indigo-600 text-white" : "bg-slate-100 text-indigo-600"}`}>
                      {msg.sender === "user" ? <Building2 className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
                    </div>
                    <div className={`max-w-xl text-sm p-3.5 rounded-xl ${
                      msg.sender === "user" 
                        ? "bg-indigo-600 text-white rounded-tr-none" 
                        : "bg-slate-50 border border-slate-100 text-slate-700 rounded-tl-none"
                    }`}>
                      {msg.text}
                    </div>
                  </div>
                ))}
              </div>
              <form onSubmit={handleSendMessage} className="p-3 border-t border-slate-200 flex gap-2">
                <input
                  type="text"
                  value={inputText}
                  onChange={(e) => setInputText(e.target.value)}
                  placeholder="Ask a question about GST, scheme eligibility, or credit readiness (Demo UI)..."
                  className="flex-1 border border-slate-200 rounded-lg px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
                <button
                  type="submit"
                  className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition flex items-center justify-center"
                >
                  <Send className="w-4 h-4" />
                </button>
              </form>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
