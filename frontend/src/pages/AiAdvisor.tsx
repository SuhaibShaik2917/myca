import React, { useState, useEffect, useRef } from "react";
import {
  Send,
  Sparkles,
  Bot,
  User,
  ShieldAlert,
  TrendingUp,
  Info,
  Loader2,
  AlertCircle,
} from "lucide-react";
import { chatWithAdvisor, getQuickInsights, QuickInsight } from "../api";

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  isFallback?: boolean;
  metrics?: Record<string, any>;
}

const SUGGESTED_PROMPTS = [
  "Why is my Financial Readiness score at its current level?",
  "Summarize my GSTR-3B tax payable and cash challan obligations.",
  "Which MSME government schemes am I most eligible for right now?",
  "How can I optimize my ITC recovery for pending invoices?",
];

function renderSafeMarkdownText(text: string) {
  return text.split("\n").map((line, i) => {
    const trimmed = line.trim();
    if (!trimmed) {
      return <div key={i} className="h-2" />;
    }
    const isBullet = trimmed.startsWith("- ") || trimmed.startsWith("* ");
    const content = isBullet ? trimmed.slice(2) : line;
    const parts = content.split(/(\*\*[^*]+\*\*)/g).map((seg, idx) =>
      seg.startsWith("**") && seg.endsWith("**") && seg.length > 4 ? (
        <strong key={idx}>{seg.slice(2, -2)}</strong>
      ) : (
        <React.Fragment key={idx}>{seg}</React.Fragment>
      )
    );
    if (isBullet) {
      return (
        <div key={i} className="flex items-start gap-2 pl-1">
          <span className="text-indigo-500 font-bold">•</span>
          <span>{parts}</span>
        </div>
      );
    }
    return <p key={i}>{parts}</p>;
  });
}

export default function AiAdvisor() {
  const [messages, setMessages] = useState<ChatMsg[]>([
    {
      role: "assistant",
      content:
        "Hello! I am MyCA, your Virtual Chartered Accountant. I analyze your live database metrics (GSTR-3B tax liabilities, Net ITC balances, Financial Readiness Scores, and Matched MSME Schemes) to provide grounded financial and compliance guidance. How can I assist you today?",
    },
  ]);
  const [insights, setInsights] = useState<QuickInsight[]>([]);
  const [insightsError, setInsightsError] = useState<string | null>(null);
  const [chatError, setChatError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const endOfMessagesRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setInsightsError(null);
    getQuickInsights()
      .then((res) => setInsights(res))
      .catch((err: any) => {
        setInsights([]);
        setInsightsError(err?.message || "Failed to load quick financial insights.");
      });
  }, []);

  useEffect(() => {
    endOfMessagesRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = async (textOverride?: string) => {
    const msg = (textOverride ?? input).trim();
    if (!msg || loading) return;

    const historyPayload = [
      ...messages.map((m) => ({ role: m.role, content: m.content })),
      { role: "user" as const, content: msg },
    ];
    setMessages((prev) => [...prev, { role: "user", content: msg }]);
    if (!textOverride) setInput("");
    setLoading(true);
    setChatError(null);

    try {
      const res = await chatWithAdvisor(historyPayload, true);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: res.reply,
          isFallback: res.model_used === "myca-gst-intelligence-engine-v2",
          metrics: res.grounding_context,
        },
      ]);
    } catch (err: any) {
      const errMsg = err?.message || "Unable to connect to the AI Advisor backend.";
      setChatError(errMsg);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `Request could not be completed: ${errMsg}`,
          isFallback: true,
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-indigo-600" />
            AI Financial Advisor &amp; Virtual CA
          </h2>
          <p className="text-sm text-slate-500 mt-1">
            Grounded conversational assistant powered by your live GST, Cash Flow, and MSME Scheme data.
          </p>
        </div>

        <div className="flex items-center gap-2 px-3 py-1.5 bg-indigo-50 border border-indigo-100 rounded-lg text-xs text-indigo-700 font-semibold">
          <Bot className="w-4 h-4" />
          <span>Context-Grounded Financial Reasoning</span>
        </div>
      </div>

      {/* Main Split Interface */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column: Quick Insights & Suggested Questions */}
        <div className="space-y-4">
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-1.5">
              <TrendingUp className="w-4 h-4 text-indigo-600" />
              Live Compliance Snapshot
            </h3>
            {insightsError && (
              <div className="mb-3 p-2.5 bg-rose-50 border border-rose-200 rounded-lg text-xs text-rose-700 flex items-start gap-2">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                <span>{insightsError}</span>
              </div>
            )}
            <div className="space-y-3">
              {insights.map((ins) => (
                <div key={ins.id} className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                  <div className="flex justify-between items-center mb-1 gap-2">
                    <span className="text-xs font-semibold text-slate-700">{ins.title}</span>
                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase ${
                        ins.severity === "critical"
                          ? "bg-rose-100 text-rose-800"
                          : ins.severity === "warning"
                          ? "bg-amber-100 text-amber-800"
                          : "bg-emerald-100 text-emerald-800"
                      }`}
                    >
                      {ins.severity}
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-600">{ins.summary}</p>
                  {ins.statutory_reference && (
                    <div className="text-[10px] text-indigo-700 font-mono mt-1">
                      Ref: {ins.statutory_reference}
                    </div>
                  )}
                </div>
              ))}
              {!insightsError && insights.length === 0 && (
                <p className="text-xs text-slate-400">
                  Sign in and select an organization to view grounded compliance metrics.
                </p>
              )}
            </div>
          </div>

          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3">
              Recommended Advisory Queries
            </h3>
            <div className="space-y-2">
              {SUGGESTED_PROMPTS.map((q, i) => (
                <button
                  key={i}
                  onClick={() => handleSend(q)}
                  disabled={loading}
                  className="w-full text-left p-2.5 text-xs bg-slate-50 hover:bg-indigo-50 text-slate-700 hover:text-indigo-800 rounded-lg border border-slate-200 hover:border-indigo-200 transition font-medium"
                >
                  &ldquo;{q}&rdquo;
                </button>
              ))}
            </div>
          </div>

          <div className="p-4 bg-amber-50 border border-amber-200 rounded-xl text-xs text-amber-800 flex items-start gap-2.5">
            <ShieldAlert className="w-4 h-4 flex-shrink-0 mt-0.5 text-amber-600" />
            <div>
              <strong>Disclaimer:</strong> MyCA AI is an advisory assistant providing guidance from live metrics. It does not substitute statutory tax audit sign-off by a practicing Chartered Accountant.
            </div>
          </div>
        </div>

        {/* Right Column: Conversational Thread */}
        <div className="lg:col-span-2 bg-white rounded-xl border border-slate-200 shadow-sm flex flex-col h-[600px]">
          {chatError && (
            <div className="px-4 py-2 bg-rose-50 border-b border-rose-200 text-xs text-rose-700 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{chatError}</span>
            </div>
          )}
          <div className="flex-1 p-5 overflow-y-auto space-y-4">
            {messages.map((m, idx) => (
              <div
                key={idx}
                className={`flex gap-3 ${m.role === "user" ? "flex-row-reverse" : "flex-row"}`}
              >
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                    m.role === "user"
                      ? "bg-slate-900 text-white"
                      : "bg-indigo-600 text-white shadow-sm"
                  }`}
                >
                  {m.role === "user" ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
                </div>

                <div
                  className={`max-w-[85%] rounded-2xl p-4 text-xs leading-relaxed ${
                    m.role === "user"
                      ? "bg-slate-900 text-white rounded-tr-none"
                      : "bg-slate-50 border border-slate-200 text-slate-800 rounded-tl-none"
                  }`}
                >
                  <div className="space-y-1">{renderSafeMarkdownText(m.content)}</div>

                  {m.isFallback && (
                    <div className="mt-2 pt-2 border-t border-slate-200/70 flex items-center gap-1.5 text-[10px] text-slate-500">
                      <Info className="w-3 h-3 text-indigo-500" />
                      <span>
                        Response computed directly from verified database metrics (Deterministic Advisor Mode).
                      </span>
                    </div>
                  )}
                </div>
              </div>
            ))}

            {loading && (
              <div className="flex gap-3">
                <div className="w-8 h-8 rounded-full bg-indigo-600 text-white flex items-center justify-center">
                  <Bot className="w-4 h-4" />
                </div>
                <div className="bg-slate-50 border border-slate-200 rounded-2xl rounded-tl-none p-4 text-xs text-slate-500 flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin text-indigo-600" />
                  <span>Analyzing tax ledger and readiness metrics...</span>
                </div>
              </div>
            )}
            <div ref={endOfMessagesRef} />
          </div>

          <div className="p-4 border-t border-slate-100 bg-slate-50 rounded-b-xl">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSend();
              }}
              className="flex gap-2"
            >
              <input
                type="text"
                placeholder="Ask MyCA about GST compliance, working capital, credit readiness, or MSME schemes..."
                className="flex-1 bg-white border border-slate-300 rounded-lg px-4 py-2.5 text-xs focus:ring-2 focus:ring-indigo-500 outline-none shadow-inner"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={loading}
              />
              <button
                type="submit"
                disabled={loading || !input.trim()}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold flex items-center gap-2 shadow-sm transition"
              >
                <Send className="w-3.5 h-3.5" />
                <span>Send</span>
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
