import React, { useState, useEffect, useCallback } from "react";
import {
  FileText,
  UploadCloud,
  CheckCircle2,
  XCircle,
  BookOpen,
  GitCompare,
  ShieldAlert,
  FileCheck2,
  TrendingUp,
  Compass,
  Sparkles,
  RefreshCw,
  LogOut,
  Eye,
  X,
} from "lucide-react";
import { Gstr2bUpload } from "./pages/Gstr2bUpload";
import { PurchaseRegister } from "./pages/PurchaseRegister";
import { ReconciliationWorkbench } from "./pages/ReconciliationWorkbench";
import { ItcRiskDashboard } from "./pages/ItcRiskDashboard";
import Gstr3bFilingView from "./pages/Gstr3bFilingView";
import CreditBuilder from "./pages/CreditBuilder";
import SchemeNavigator from "./pages/SchemeNavigator";
import AiAdvisor from "./pages/AiAdvisor";
import {
  loadSession,
  saveSession,
  isDemoAuthEnabled,
  getCurrentUser,
  getBusinessProfile,
  logoutSession,
  requestDemoToken,
  requestIdpNonce,
  exchangeIdpCode,
  generatePkceVerifier,
  generateOidcState,
  derivePkceChallenge,
  buildOidcAuthorizeUrl,
  listInvoices,
  getInvoiceDetail,
  patchInvoice,
  uploadInvoice,
  approveInvoice,
  rejectInvoice,
  fetchGstr1Draft,
  listRegistrations,
  AuthenticatedContext,
  InvoiceItem,
  GstRegistration,
  SessionConfig,
} from "./api";

type TabKey =
  | "invoices"
  | "gstr1"
  | "purchases"
  | "gstr2b"
  | "reconcile"
  | "itc-risk"
  | "gstr3b"
  | "credit"
  | "schemes"
  | "advisor";

export function App() {
  const [activeTab, setActiveTab] = useState<TabKey>("invoices");
  const [session, setSession] = useState<SessionConfig>(() => loadSession());
  const [authContext, setAuthContext] = useState<AuthenticatedContext | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authNotice, setAuthNotice] = useState<string | null>(null);

  const [invoices, setInvoices] = useState<InvoiceItem[]>([]);
  const [registrations, setRegistrations] = useState<GstRegistration[]>([]);
  const [gstr1Draft, setGstr1Draft] = useState<Record<string, unknown> | null>(null);
  const [loadingInvoices, setLoadingInvoices] = useState(false);
  const [invoiceFile, setInvoiceFile] = useState<File | null>(null);

  // Invoice Review Modal State
  const [inspectInvoice, setInspectInvoice] = useState<InvoiceItem | null>(null);
  const [editCounterpartyGstin, setEditCounterpartyGstin] = useState("");
  const [editCounterpartyName, setEditCounterpartyName] = useState("");
  const [editInvNum, setEditInvNum] = useState("");
  const [editTotal, setEditTotal] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [showRejectBox, setShowRejectBox] = useState<string | null>(null);

  // Federated OIDC Authorization Code + PKCE Settings
  const [oidcOrgId, setOidcOrgId] = useState(
    () => localStorage.getItem("myca.oidc.orgId") || loadSession().orgId || ""
  );
  const [oidcAuthorizeUrl, setOidcAuthorizeUrl] = useState(
    () => localStorage.getItem("myca.oidc.authorizeUrl") || ""
  );
  const [oidcClientId, setOidcClientId] = useState(
    () => localStorage.getItem("myca.oidc.clientId") || ""
  );
  const [oidcRedirectUri, setOidcRedirectUri] = useState(
    () => localStorage.getItem("myca.oidc.redirectUri") || `${window.location.origin}/auth/callback`
  );
  const demoAllowed = isDemoAuthEnabled();

  const handleSaveSession = (patch: Partial<SessionConfig>) => {
    const next = saveSession(patch);
    setSession(next);
  };

  const resolveProfileIdForReg = useCallback(
    async (me: AuthenticatedContext | null, regId: string): Promise<string> => {
      const regGstin = me?.registrations?.find((r) => r.id === regId)?.gstin;
      const profiles = me?.profiles || [];
      if (regGstin) {
        const matched = profiles.find((p) => (p.gstin || "").toUpperCase() === regGstin.toUpperCase());
        if (matched) return matched.id;
      }
      if (profiles[0]?.id) return profiles[0].id;
      try {
        const bp = await getBusinessProfile();
        return bp.id;
      } catch {
        return "";
      }
    },
    []
  );

  const refreshContextAndWorkspace = useCallback(async () => {
    const cur = loadSession();
    if (!cur.token && (!demoAllowed || !cur.orgId || !cur.userId)) {
      return;
    }
    setAuthError(null);
    setLoadingInvoices(true);
    try {
      const me = await getCurrentUser();
      setAuthContext(me);
      setRegistrations(me.registrations || []);
      const defaultReg =
        me.registrations?.find((r) => r.id === cur.registrationId)?.id ||
        me.registrations?.find((r) => r.is_primary)?.id ||
        me.registrations?.[0]?.id ||
        "";
      const defaultProfileId = await resolveProfileIdForReg(me, defaultReg);
      const nextSess = saveSession({
        orgId: me.org_id,
        userId: me.user_id || cur.userId,
        email: me.email || cur.email,
        role: me.role,
        pan: me.organization?.pan || "",
        registrationId: defaultReg,
        profileId: defaultProfileId,
      });
      setSession(nextSess);

      const invList = await listInvoices();
      setInvoices(invList);
    } catch (err: any) {
      setAuthError(err?.message || "Failed to load organization workspace.");
      try {
        const [invList, regList] = await Promise.all([listInvoices(), listRegistrations()]);
        setInvoices(invList);
        setRegistrations(regList);
      } catch {
        // leave empty cleanly
      }
    } finally {
      setLoadingInvoices(false);
    }
  }, [demoAllowed, resolveProfileIdForReg]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const returnedState = params.get("state");
    const savedState = sessionStorage.getItem("myca.oidc.state");
    const savedVerifier = sessionStorage.getItem("myca.oidc.code_verifier");
    const savedRedirectUri = sessionStorage.getItem("myca.oidc.redirect_uri");
    const savedProvider = sessionStorage.getItem("myca.oidc.provider") || session.idpProvider || "google";
    const savedOrgId = sessionStorage.getItem("myca.oidc.org_id") || oidcOrgId || session.orgId;

    if (code && returnedState) {
      window.history.replaceState({}, document.title, window.location.pathname);
      if (!savedState || returnedState !== savedState || !savedVerifier || !savedOrgId || !savedRedirectUri) {
        setAuthError("OIDC state mismatch or missing PKCE verifier/org context. Restart sign-in.");
        return;
      }
      exchangeIdpCode({
        provider: savedProvider,
        orgId: savedOrgId,
        code,
        state: returnedState,
        codeVerifier: savedVerifier,
        redirectUri: savedRedirectUri,
      })
        .then((res) => {
          sessionStorage.removeItem("myca.oidc.state");
          sessionStorage.removeItem("myca.oidc.code_verifier");
          sessionStorage.removeItem("myca.oidc.redirect_uri");
          sessionStorage.removeItem("myca.oidc.provider");
          sessionStorage.removeItem("myca.oidc.org_id");
          setSession(loadSession());
          setAuthNotice(
            `Authenticated via ${res.provider || savedProvider} (${res.role}) for org ${res.org_id}.`
          );
          refreshContextAndWorkspace();
        })
        .catch((err: any) => {
          setAuthError(err?.message || "OIDC authorization code exchange failed.");
        });
      return;
    }

    refreshContextAndWorkspace();
  }, [refreshContextAndWorkspace]);

  const handleOidcSignIn = async () => {
    setAuthError(null);
    setAuthNotice(null);
    try {
      const targetOrgId = oidcOrgId.trim();
      if (!targetOrgId || !oidcAuthorizeUrl || !oidcClientId || !oidcRedirectUri) {
        throw new Error(
          "Enter Organization UUID, IdP Authorize URL, Client ID, and Redirect URI to launch OIDC PKCE login."
        );
      }
      const verifier = generatePkceVerifier();
      const challenge = await derivePkceChallenge(verifier);
      const clientState = generateOidcState();
      const provider = session.idpProvider || "google";

      const nonceRes = await requestIdpNonce({
        orgId: targetOrgId,
        provider,
        state: clientState,
        codeChallenge: challenge,
      });

      localStorage.setItem("myca.oidc.orgId", targetOrgId);
      localStorage.setItem("myca.oidc.authorizeUrl", oidcAuthorizeUrl);
      localStorage.setItem("myca.oidc.clientId", oidcClientId);
      localStorage.setItem("myca.oidc.redirectUri", oidcRedirectUri);
      sessionStorage.setItem("myca.oidc.state", nonceRes.state);
      sessionStorage.setItem("myca.oidc.code_verifier", verifier);
      sessionStorage.setItem("myca.oidc.redirect_uri", oidcRedirectUri);
      sessionStorage.setItem("myca.oidc.provider", provider);
      sessionStorage.setItem("myca.oidc.org_id", targetOrgId);

      const authorizeTarget = buildOidcAuthorizeUrl({
        authorizeEndpoint: oidcAuthorizeUrl,
        clientId: oidcClientId,
        redirectUri: oidcRedirectUri,
        state: nonceRes.state,
        nonce: nonceRes.nonce,
        codeChallenge: challenge,
      });
      window.location.assign(authorizeTarget);
    } catch (err: any) {
      setAuthError(err?.message || "OIDC sign-in initialization failed.");
    }
  };

  const handleIssueDemoToken = async () => {
    setAuthError(null);
    setAuthNotice(null);
    try {
      if (!session.orgId || !session.userId) {
        throw new Error("Provide Org ID and User ID to request a dev token.");
      }
      const res = await requestDemoToken(session.orgId, session.userId);
      setSession(loadSession());
      setAuthNotice(`Issued signed HS256 dev Bearer token (${res.role}, ${res.expires_in}s).`);
      await refreshContextAndWorkspace();
    } catch (err: any) {
      setAuthError(err?.message || "Failed to issue dev token.");
    }
  };

  const handleSignOut = async () => {
    setAuthError(null);
    await logoutSession();
    setSession(loadSession());
    setAuthContext(null);
    setInvoices([]);
    setRegistrations([]);
    setGstr1Draft(null);
    setAuthNotice("Session revoked and signed out.");
  };

  const handleUploadInvoice = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!invoiceFile) return;
    setAuthError(null);
    try {
      const pid = session.profileId || (await resolveProfileIdForReg(authContext, session.registrationId));
      const res = await uploadInvoice(invoiceFile, pid);
      setAuthNotice(`Invoice uploaded (${res.invoice_id.slice(0, 8)}..., status: ${res.status}).`);
      setInvoiceFile(null);
      await refreshContextAndWorkspace();
    } catch (err: any) {
      setAuthError(err?.message || "Invoice upload failed.");
    }
  };

  const handleApproveInvoice = async (id: string) => {
    setAuthError(null);
    try {
      await approveInvoice(id);
      setAuthNotice(`Invoice ${id.slice(0, 8)}... approved.`);
      await refreshContextAndWorkspace();
    } catch (err: any) {
      setAuthError(err?.message || "Approval failed.");
    }
  };

  const handleRejectInvoice = async (id: string) => {
    if (!rejectReason.trim()) {
      setAuthError("Rejection reason is required.");
      return;
    }
    setAuthError(null);
    try {
      await rejectInvoice(id, rejectReason.trim());
      setShowRejectBox(null);
      setRejectReason("");
      setAuthNotice(`Invoice ${id.slice(0, 8)}... rejected.`);
      await refreshContextAndWorkspace();
    } catch (err: any) {
      setAuthError(err?.message || "Rejection failed.");
    }
  };

  const handleOpenInspect = async (id: string) => {
    setAuthError(null);
    try {
      const detail = await getInvoiceDetail(id);
      setInspectInvoice(detail);
      setEditCounterpartyGstin(detail.counterparty_gstin || "");
      setEditCounterpartyName(detail.counterparty_name || "");
      setEditInvNum(detail.invoice_number || "");
      setEditTotal(String(detail.total_amount ?? ""));
    } catch (err: any) {
      setAuthError(err?.message || "Failed to load invoice details.");
    }
  };

  const handleSaveInspectEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inspectInvoice) return;
    setAuthError(null);
    try {
      await patchInvoice(inspectInvoice.id, {
        invoice_number: editInvNum.trim(),
        counterparty_gstin: editCounterpartyGstin.trim() || null,
        counterparty_name: editCounterpartyName.trim() || null,
        total_amount: parseFloat(editTotal) || undefined,
      });
      setInspectInvoice(null);
      setAuthNotice("Invoice updated and validation report refreshed.");
      await refreshContextAndWorkspace();
    } catch (err: any) {
      setAuthError(err?.message || "Failed to save invoice edits.");
    }
  };

  const handleLoadGstr1 = async () => {
    setAuthError(null);
    try {
      const pid = session.profileId || (await resolveProfileIdForReg(authContext, session.registrationId));
      const draft = await fetchGstr1Draft(session.returnPeriod || "2026-11", pid || undefined);
      setGstr1Draft(draft);
    } catch (err: any) {
      setAuthError(err?.message || "Failed to generate GSTR-1 working draft.");
    }
  };

  const tabs: Array<{ key: TabKey; label: string; icon: React.ReactNode }> = [
    { key: "invoices", label: "Outward Invoices", icon: <FileText className="w-4 h-4" /> },
    { key: "gstr1", label: "GSTR-1 Draft", icon: <FileCheck2 className="w-4 h-4" /> },
    { key: "purchases", label: "Purchase Register", icon: <BookOpen className="w-4 h-4" /> },
    { key: "gstr2b", label: "GSTR-2B & IMS", icon: <UploadCloud className="w-4 h-4" /> },
    { key: "reconcile", label: "2B vs Books", icon: <GitCompare className="w-4 h-4" /> },
    { key: "itc-risk", label: "ITC Risk & Table 4", icon: <ShieldAlert className="w-4 h-4" /> },
    { key: "gstr3b", label: "GSTR-3B Filing", icon: <FileCheck2 className="w-4 h-4" /> },
    { key: "credit", label: "Credit Builder", icon: <TrendingUp className="w-4 h-4" /> },
    { key: "schemes", label: "Schemes", icon: <Compass className="w-4 h-4" /> },
    { key: "advisor", label: "Virtual CA AI", icon: <Sparkles className="w-4 h-4" /> },
  ];

  const isAuthenticated = Boolean(session.token || authContext);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      {/* Top Bar */}
      <header className="bg-slate-900 text-white px-6 py-4 shadow">
        <div className="max-w-7xl mx-auto flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold tracking-tight flex items-center gap-2">
              MyCA
              <span className="text-[11px] font-mono bg-indigo-500/20 text-indigo-200 px-2 py-0.5 rounded border border-indigo-400/30">
                GST &amp; MSME Compliance
              </span>
            </h1>
            <p className="text-xs text-slate-400">
              {authContext
                ? `${authContext.organization?.legal_name || "Organization"} (PAN: ${
                    authContext.organization?.pan || "N/A"
                  }) • Role: ${authContext.role.toUpperCase()}`
                : "Sign in via OIDC PKCE to bind your organization workspace"}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <input
              type="text"
              placeholder="API Base (empty = same-origin /api)"
              value={session.apiBase}
              onChange={(e) => handleSaveSession({ apiBase: e.target.value })}
              className="px-2.5 py-1.5 rounded bg-slate-800 border border-slate-700 text-slate-100 font-mono w-44"
            />
            {registrations.length > 0 && (
              <select
                value={session.registrationId}
                onChange={async (e) => {
                  const regId = e.target.value;
                  const pid = await resolveProfileIdForReg(authContext, regId);
                  handleSaveSession({ registrationId: regId, profileId: pid });
                }}
                className="px-2.5 py-1.5 rounded bg-slate-800 border border-slate-700 text-slate-100 font-mono"
              >
                {registrations.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.gstin} ({r.state_code})
                  </option>
                ))}
              </select>
            )}
            <input
              type="month"
              value={session.returnPeriod}
              onChange={(e) => handleSaveSession({ returnPeriod: e.target.value })}
              className="px-2.5 py-1.5 rounded bg-slate-800 border border-slate-700 text-slate-100 font-mono"
            />
            <button
              onClick={refreshContextAndWorkspace}
              className="px-3 py-1.5 rounded bg-indigo-600 hover:bg-indigo-500 font-semibold flex items-center gap-1"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loadingInvoices ? "animate-spin" : ""}`} />
              Sync Workspace
            </button>
            {isAuthenticated && (
              <button
                onClick={handleSignOut}
                className="px-3 py-1.5 rounded bg-slate-700 hover:bg-slate-600 font-semibold flex items-center gap-1"
              >
                <LogOut className="w-3.5 h-3.5" />
                Sign Out
              </button>
            )}
          </div>
        </div>

        {/* Auth Panel: Production OIDC PKCE + Gated Dev Token */}
        <div className="max-w-7xl mx-auto mt-3 pt-3 border-t border-slate-800 flex flex-wrap items-center gap-2 text-xs">
          <select
            value={session.idpProvider}
            onChange={(e) => handleSaveSession({ idpProvider: e.target.value })}
            className="px-2.5 py-1 rounded bg-slate-800 border border-slate-700 text-slate-100"
          >
            <option value="google">Google OIDC</option>
            <option value="azuread">Microsoft Entra ID</option>
            <option value="okta">Okta</option>
            <option value="auth0">Auth0</option>
          </select>
          <input
            type="text"
            placeholder="Organization UUID"
            value={oidcOrgId}
            onChange={(e) => setOidcOrgId(e.target.value)}
            className="px-2.5 py-1 rounded bg-slate-800 border border-slate-700 text-slate-100 font-mono w-44"
          />
          <input
            type="text"
            placeholder="OIDC Authorize URL (https://...)"
            value={oidcAuthorizeUrl}
            onChange={(e) => setOidcAuthorizeUrl(e.target.value)}
            className="px-2.5 py-1 rounded bg-slate-800 border border-slate-700 text-slate-100 font-mono w-52"
          />
          <input
            type="text"
            placeholder="OIDC Client ID"
            value={oidcClientId}
            onChange={(e) => setOidcClientId(e.target.value)}
            className="px-2.5 py-1 rounded bg-slate-800 border border-slate-700 text-slate-100 font-mono w-36"
          />
          <input
            type="text"
            placeholder="Redirect URI"
            value={oidcRedirectUri}
            onChange={(e) => setOidcRedirectUri(e.target.value)}
            className="px-2.5 py-1 rounded bg-slate-800 border border-slate-700 text-slate-100 font-mono w-48"
          />
          <button
            onClick={handleOidcSignIn}
            className="px-3 py-1 rounded bg-emerald-600 hover:bg-emerald-500 font-semibold"
          >
            Sign in with OIDC (PKCE)
          </button>

          {demoAllowed && (
            <>
              <input
                type="text"
                placeholder="Dev Org UUID"
                value={session.orgId}
                onChange={(e) => handleSaveSession({ orgId: e.target.value })}
                className="px-2 py-1 rounded bg-slate-800 border border-slate-700 text-slate-100 font-mono w-32"
              />
              <input
                type="text"
                placeholder="Dev User UUID"
                value={session.userId}
                onChange={(e) => handleSaveSession({ userId: e.target.value })}
                className="px-2 py-1 rounded bg-slate-800 border border-slate-700 text-slate-100 font-mono w-32"
              />
              <button
                onClick={handleIssueDemoToken}
                className="px-3 py-1 rounded bg-amber-600 hover:bg-amber-500 font-semibold"
              >
                Issue Dev Token
              </button>
            </>
          )}
          <span className="ml-auto text-[11px] font-mono text-slate-400">
            Auth: {session.token ? `Bearer Active (${session.role || "member"})` : "Signed Out"}
          </span>
        </div>
      </header>

      {/* Navigation Tabs */}
      <nav className="bg-white border-b border-slate-200">
        <div className="max-w-7xl mx-auto px-6 flex overflow-x-auto gap-1 py-2">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`px-3 py-2 rounded-lg text-xs font-semibold flex items-center gap-1.5 whitespace-nowrap transition ${
                activeTab === tab.key
                  ? "bg-indigo-600 text-white shadow-sm"
                  : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              {tab.icon}
              <span>{tab.label}</span>
            </button>
          ))}
        </div>
      </nav>

      {/* Alerts */}
      <div className="max-w-7xl mx-auto px-6 pt-4 space-y-2">
        {authError && (
          <div className="p-3 bg-rose-50 border border-rose-200 rounded-lg text-xs text-rose-800">
            {authError}
          </div>
        )}
        {authNotice && (
          <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-lg text-xs text-emerald-800">
            {authNotice}
          </div>
        )}
      </div>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-6 py-6">
        {activeTab === "invoices" && (
          <div className="space-y-6">
            <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
              <h2 className="text-lg font-bold text-slate-900 mb-2">Outward Invoice Brain &amp; OCR Review</h2>
              <p className="text-xs text-slate-500 mb-4">
                Upload sales invoices (PDF/Image). Validations follow strict Section 37 / Rule 46 GST rules before Maker-Checker approval.
              </p>
              <form onSubmit={handleUploadInvoice} className="flex flex-wrap items-center gap-3">
                <input
                  type="file"
                  accept=".pdf,.png,.jpg,.jpeg"
                  onChange={(e) => setInvoiceFile(e.target.files?.[0] || null)}
                  className="text-xs"
                />
                <button
                  type="submit"
                  disabled={!invoiceFile}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold"
                >
                  Upload &amp; Enqueue OCR
                </button>
              </form>
            </div>

            <div className="bg-white rounded-xl border border-slate-200 overflow-hidden shadow-sm">
              <div className="px-6 py-4 border-b border-slate-100 flex justify-between items-center">
                <h3 className="text-sm font-bold text-slate-800">Outward Invoice Queue ({invoices.length})</h3>
              </div>
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
                  <tr>
                    <th className="px-4 py-3">Invoice No</th>
                    <th className="px-4 py-3">Date</th>
                    <th className="px-4 py-3">Counterparty GSTIN</th>
                    <th className="px-4 py-3 text-right">Amount (₹)</th>
                    <th className="px-4 py-3 text-center">Status</th>
                    <th className="px-4 py-3 text-center">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 font-mono">
                  {invoices.map((inv) => (
                    <tr key={inv.id} className="hover:bg-slate-50">
                      <td className="px-4 py-3 font-bold">{inv.invoice_number}</td>
                      <td className="px-4 py-3">{inv.invoice_date}</td>
                      <td className="px-4 py-3">{inv.counterparty_gstin || "B2C"}</td>
                      <td className="px-4 py-3 text-right">₹{inv.total_amount}</td>
                      <td className="px-4 py-3 text-center font-sans">
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            inv.status === "approved"
                              ? "bg-emerald-100 text-emerald-800"
                              : inv.status === "rejected"
                              ? "bg-rose-100 text-rose-800"
                              : inv.status === "pending_review"
                              ? "bg-amber-100 text-amber-800"
                              : "bg-slate-100 text-slate-700"
                          }`}
                        >
                          {inv.status.replace(/_/g, " ").toUpperCase()}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-center font-sans space-x-1">
                        <button
                          onClick={() => handleOpenInspect(inv.id)}
                          className="px-2 py-1 bg-slate-100 hover:bg-slate-200 text-slate-800 rounded text-[11px] font-semibold inline-flex items-center gap-1"
                        >
                          <Eye className="w-3 h-3" /> Inspect
                        </button>
                        {inv.status === "pending_review" && (
                          <>
                            <button
                              onClick={() => handleApproveInvoice(inv.id)}
                              className="px-2 py-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-[11px] font-semibold inline-flex items-center gap-1"
                            >
                              <CheckCircle2 className="w-3 h-3" /> Approve
                            </button>
                            <button
                              onClick={() => {
                                setShowRejectBox(inv.id);
                                setRejectReason("");
                              }}
                              className="px-2 py-1 bg-rose-600 hover:bg-rose-700 text-white rounded text-[11px] font-semibold inline-flex items-center gap-1"
                            >
                              <XCircle className="w-3 h-3" /> Reject
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                  {invoices.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-4 py-8 text-center font-sans text-slate-400">
                        No outward invoices found for this tenant. Sign in above and upload an invoice or sync workspace.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {showRejectBox && (
              <div className="bg-rose-50 border border-rose-200 rounded-xl p-4 flex flex-wrap items-center gap-2 text-xs">
                <input
                  type="text"
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  placeholder="Enter statutory rejection reason..."
                  className="flex-1 min-w-64 px-3 py-1.5 bg-white border border-rose-300 rounded"
                />
                <button
                  onClick={() => handleRejectInvoice(showRejectBox)}
                  className="px-3 py-1.5 bg-rose-600 text-white rounded font-semibold"
                >
                  Confirm Rejection
                </button>
                <button
                  onClick={() => setShowRejectBox(null)}
                  className="px-3 py-1.5 bg-white border border-slate-300 rounded"
                >
                  Cancel
                </button>
              </div>
            )}

            {inspectInvoice && (
              <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
                <div className="bg-white rounded-xl shadow-xl border border-slate-200 max-w-2xl w-full p-6 space-y-4 max-h-[85vh] overflow-y-auto">
                  <div className="flex justify-between items-center border-b border-slate-100 pb-3">
                    <h3 className="text-sm font-bold text-slate-900">
                      Invoice Details &amp; Validation Report — {inspectInvoice.invoice_number}
                    </h3>
                    <button onClick={() => setInspectInvoice(null)} className="text-slate-400 hover:text-slate-700">
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                  {inspectInvoice.validation_report && (
                    <div
                      className={`p-3 rounded-lg text-xs border ${
                        inspectInvoice.validation_report.is_valid
                          ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                          : "bg-amber-50 border-amber-200 text-amber-900"
                      }`}
                    >
                      <strong>Validation Status:</strong>{" "}
                      {inspectInvoice.validation_report.is_valid
                        ? "Passed Statutory Rule Checks"
                        : (inspectInvoice.validation_report.flags || [])
                            .map((f) => `[${f.severity.toUpperCase()}] ${f.message}`)
                            .join("; ") || "Requires Human Review"}
                    </div>
                  )}
                  <form onSubmit={handleSaveInspectEdit} className="grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <label className="block font-semibold text-slate-600 mb-1">Invoice Number</label>
                      <input
                        type="text"
                        value={editInvNum}
                        onChange={(e) => setEditInvNum(e.target.value)}
                        disabled={inspectInvoice.status === "approved"}
                        className="w-full px-3 py-1.5 border border-slate-300 rounded font-mono"
                      />
                    </div>
                    <div>
                      <label className="block font-semibold text-slate-600 mb-1">Counterparty GSTIN</label>
                      <input
                        type="text"
                        value={editCounterpartyGstin}
                        onChange={(e) => setEditCounterpartyGstin(e.target.value.toUpperCase())}
                        disabled={inspectInvoice.status === "approved"}
                        className="w-full px-3 py-1.5 border border-slate-300 rounded font-mono"
                      />
                    </div>
                    <div>
                      <label className="block font-semibold text-slate-600 mb-1">Counterparty Name</label>
                      <input
                        type="text"
                        value={editCounterpartyName}
                        onChange={(e) => setEditCounterpartyName(e.target.value)}
                        disabled={inspectInvoice.status === "approved"}
                        className="w-full px-3 py-1.5 border border-slate-300 rounded"
                      />
                    </div>
                    <div>
                      <label className="block font-semibold text-slate-600 mb-1">Total Amount (₹)</label>
                      <input
                        type="number"
                        step="0.01"
                        value={editTotal}
                        onChange={(e) => setEditTotal(e.target.value)}
                        disabled={inspectInvoice.status === "approved"}
                        className="w-full px-3 py-1.5 border border-slate-300 rounded font-mono"
                      />
                    </div>
                    {inspectInvoice.status !== "approved" && (
                      <div className="col-span-2 flex justify-end gap-2 pt-2">
                        <button
                          type="submit"
                          className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded font-semibold"
                        >
                          Save Corrections
                        </button>
                      </div>
                    )}
                  </form>
                </div>
              </div>
            )}
          </div>
        )}

        {activeTab === "gstr1" && (
          <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-bold text-slate-900">GSTR-1 Working Draft</h2>
                <p className="text-xs text-slate-500">
                  Generated exclusively from human-approved outward invoices for the active business profile &amp; period.
                </p>
              </div>
              <button
                onClick={handleLoadGstr1}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-semibold"
              >
                Load GSTR-1 Draft
              </button>
            </div>
            <pre className="p-4 bg-slate-900 text-slate-100 rounded-lg text-xs overflow-auto max-h-[520px]">
              {gstr1Draft
                ? JSON.stringify(gstr1Draft, null, 2)
                : "Click 'Load GSTR-1 Draft' after syncing workspace to inspect B2B, B2CL, B2CS, HSN, and Doc summary."}
            </pre>
          </div>
        )}

        {activeTab === "purchases" && (
          <PurchaseRegister
            registrations={registrations}
            orgPan={authContext?.organization?.pan || session.pan || ""}
          />
        )}
        {activeTab === "gstr2b" && <Gstr2bUpload registrations={registrations} />}
        {activeTab === "reconcile" && <ReconciliationWorkbench registrations={registrations} />}
        {activeTab === "itc-risk" && <ItcRiskDashboard registrations={registrations} />}
        {activeTab === "gstr3b" && <Gstr3bFilingView registrations={registrations} />}
        {activeTab === "credit" && <CreditBuilder />}
        {activeTab === "schemes" && <SchemeNavigator />}
        {activeTab === "advisor" && <AiAdvisor />}
      </main>
    </div>
  );
}

export default App;
