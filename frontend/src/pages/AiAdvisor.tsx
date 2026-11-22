import React, { useState, useEffect } from "react";
import { 
  Bot, 
  Send, 
  Building2, 
  Sparkles, 
  ShieldAlert, 
  Info, 
  CheckCircle2, 
  AlertTriangle,
  FileText,
  RefreshCw
} from "lucide-react";
import { 
  sendAiChat, 
  getQuickInsights, 
  QuickInsightOut, 
  ChatResponseOut 
} from "../api";

interface Message {
  role: "user" | "assistant";
  content: string;
  groundingContext?: any;
}

export function AiAdvisor() {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content: "Namaste! I am your **MyCA AI Statutory Copilot**, grounded in your organization's real-time GST filings, ITC ledgers, and MSME credit readiness score. How can I assist you with your tax compliance, credit building, or scheme matching today?"
    }
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [insights, setInsights] = useState<QuickInsightOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  const suggestedPrompts = [
    "Explain Rule 37 (180-day payment) vs Rule 37A",
    "How does Rule 88A credit offset work for GSTR-3B?",
    "Summarize our latest tax liability and eligible ITC",
    "What government loan schemes are we eligible for?"
  ];

  const loadInsights = async () => {
    try {
      const res = await getQuickInsights();
      setInsights(res);
    } catch {
      // Non-blocking fallback
    }
  };

  useEffect(() => {
    loadInsights();
  }, []);

  const handleSend = async (userText: string) => {
    if (!userText.trim() || loading) return;

    const newMessages: Message[] = [...messages, { role: "user", content: userText }];
    setMessages(newMessages);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const apiPayload = newMessages.map((m) => ({ role: m.role, content: m.content }));
      const res: ChatResponseOut = await sendAiChat(apiPayload, true);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: res.reply,
          groundingContext: res.grounding_context
        }
      ]);
    } catch (err: any) {
      setError(err?.message || "Failed to generate AI response.");
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: "I encountered an error retrieving live accounting context. Please check your session connection or try again."
        }
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-xl md:text-2xl font-bold text-slate-800 flex items-center gap-2">
          <Bot className="w-6 h-6 text-indigo-600" />
          AI Financial & Statutory Advisor
        </h2>
        <p className="text-sm text-slate-500">
          Grounded in PostgreSQL 16 RLS Statutory Ledgers & Google Gemini Domain Reasoning
        </p>
      </div>

      {/* Proactive Statutory Alert Cards */}
      {insights.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          {insights.map((ins) => (
            <div 
              key={ins.id}
              className={`p-3.5 rounded-xl border text-xs shadow-sm flex flex-col justify-between ${
                ins.severity === "warning" ? "bg-amber-50/60 border-amber-200 text-amber-900" :
                ins.severity === "critical" ? "bg-rose-50/60 border-rose-200 text-rose-900" :
                ins.severity === "success" ? "bg-emerald-50/60 border-emerald-200 text-emerald-900" :
                "bg-indigo-50/60 border-indigo-200 text-indigo-900"
              }`}
            >
              <div>
                <div className="flex items-center gap-1.5 font-bold mb-1">
                  {ins.severity === "warning" ? <AlertTriangle className="w-3.5 h-3.5 text-amber-600 flex-shrink-0" /> :
                   ins.severity === "success" ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 flex-shrink-0" /> :
                   <Info className="w-3.5 h-3.5 text-indigo-600 flex-shrink-0" />}
                  <span className="truncate">{ins.title}</span>
                </div>
                <p className="text-[11px] leading-relaxed opacity-90">{ins.summary}</p>
              </div>
              {ins.statutory_reference && (
                <div className="mt-2 text-[10px] font-mono font-semibold opacity-75">
                  Ref: {ins.statutory_reference}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Main Chat Interface */}
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm flex flex-col h-[540px] overflow-hidden">
        {/* Messages Scroll Area */}
        <div className="flex-1 p-5 overflow-y-auto space-y-4">
          {messages.map((m, idx) => (
            <div 
              key={idx}
              className={`flex items-start gap-3 ${m.role === "user" ? "flex-row-reverse" : ""}`}
            >
              <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                m.role === "user" 
                  ? "bg-indigo-600 text-white" 
                  : "bg-slate-100 text-indigo-600 border border-slate-200"
              }`}>
                {m.role === "user" ? <Building2 className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
              </div>

              <div className={`max-w-2xl text-xs md:text-sm p-4 rounded-2xl leading-relaxed whitespace-pre-wrap ${
                m.role === "user"
                  ? "bg-indigo-600 text-white rounded-tr-none shadow-sm"
                  : "bg-slate-50 border border-slate-200 text-slate-800 rounded-tl-none shadow-sm"
              }`}>
                {m.content}

                {/* Grounding Context pill if available */}
                {m.groundingContext && (
                  <div className="mt-3 pt-2.5 border-t border-slate-200/60 text-[11px] text-slate-500 font-medium flex items-center gap-1.5">
                    <Sparkles className="w-3 h-3 text-indigo-500" />
                    <span>Grounded in {m.groundingContext.org_legal_name} ({m.groundingContext.gstin || 'Active'})</span>
                  </div>
                )}
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex items-center gap-2 text-xs text-slate-400 p-2">
              <RefreshCw className="w-3.5 h-3.5 animate-spin text-indigo-600" />
              <span>Analyzing statutory provisions and ledger balances...</span>
            </div>
          )}
        </div>

        {/* Suggested Prompts */}
        <div className="px-4 py-2 border-t border-slate-100 bg-slate-50 flex items-center gap-2 overflow-x-auto scrollbar-none">
          <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider whitespace-nowrap">Suggested:</span>
          {suggestedPrompts.map((p, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => handleSend(p)}
              className="text-xs bg-white hover:bg-indigo-50 hover:text-indigo-600 text-slate-600 border border-slate-200 px-3 py-1 rounded-full whitespace-nowrap transition shadow-2xs font-medium"
            >
              {p}
            </button>
          ))}
        </div>

        {/* Input Form */}
        <form 
          onSubmit={(e) => { e.preventDefault(); handleSend(input); }} 
          className="p-3 bg-white border-t border-slate-200 flex gap-2"
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={loading}
            placeholder="Ask a question about GST rules, GSTR-3B offset, credit readiness, or government subsidies..."
            className="flex-1 px-4 py-2 text-xs md:text-sm border border-slate-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:bg-slate-100"
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-xl text-xs font-semibold flex items-center gap-1.5 transition shadow-sm"
          >
            <Send className="w-3.5 h-3.5" />
            <span>Send</span>
          </button>
        </form>
      </div>
    </div>
  );
}
