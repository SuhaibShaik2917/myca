export const DEFAULT_API_BASE =
  import.meta.env.VITE_API_BASE_URL ??
  (import.meta.env.PROD ? "" : "http://127.0.0.1:8000");

export const STORAGE_KEY = "myca.session.v1";

export interface SessionConfig {
  apiBase: string;
  orgId: string;
  userId: string;
  registrationId: string;
  profileId: string;
  snapshotId: string;
  returnPeriod: string;
  token: string;
  idpProvider: string;
  idpSubject: string;
  email: string;
  role?: string;
  pan?: string;
}

export interface OrganizationInfo {
  id: string;
  legal_name?: string | null;
  trade_name?: string | null;
  pan?: string | null;
}

export interface GstRegistration {
  id: string;
  gstin: string;
  state_code: string;
  legal_name: string;
  trade_name?: string | null;
  registration_type?: string;
  is_primary: boolean;
}

export interface BusinessProfileSummary {
  id: string;
  gstin?: string | null;
}

export interface AuthenticatedContext {
  user_id: string | null;
  org_id: string;
  role: string;
  email?: string | null;
  full_name?: string | null;
  organization: OrganizationInfo;
  registrations: GstRegistration[];
  profiles?: BusinessProfileSummary[];
}

export function isDemoAuthEnabled(): boolean {
  const mode = String(import.meta.env.MODE || "").toLowerCase();
  const flag = String(import.meta.env.VITE_ENABLE_DEMO_AUTH || "").toLowerCase();
  if (mode === "production") {
    return false;
  }
  return flag === "true" || flag === "1";
}

function resolveSessionApiBase(rawBase?: string | null): string {
  const cleaned = String(rawBase || "").trim();
  if (!cleaned) return DEFAULT_API_BASE;
  if (
    import.meta.env.PROD &&
    (cleaned.startsWith("http://127.0.0.1") || cleaned.startsWith("http://localhost"))
  ) {
    return DEFAULT_API_BASE;
  }
  return cleaned;
}

export function loadSession(): SessionConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return {
        apiBase: resolveSessionApiBase(parsed.apiBase),
        orgId: parsed.orgId || "",
        userId: parsed.userId || "",
        registrationId: parsed.registrationId || "",
        profileId: parsed.profileId || "",
        snapshotId: parsed.snapshotId || "",
        returnPeriod: parsed.returnPeriod || "2026-11",
        token: parsed.token || "",
        idpProvider: parsed.idpProvider || "google",
        idpSubject: parsed.idpSubject || "",
        email: parsed.email || "",
        role: parsed.role || "",
        pan: parsed.pan || "",
      };
    }
  } catch {
    // ignore storage parse failure
  }
  return {
    apiBase: DEFAULT_API_BASE,
    orgId: "",
    userId: "",
    registrationId: "",
    profileId: "",
    snapshotId: "",
    returnPeriod: "2026-11",
    token: "",
    idpProvider: "google",
    idpSubject: "",
    email: "",
    role: "",
    pan: "",
  };
}

export function saveSession(patch: Partial<SessionConfig>): SessionConfig {
  const merged = { ...loadSession(), ...patch };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
  } catch {
    // ignore storage quota errors
  }
  return merged;
}

export function clearSession(): SessionConfig {
  const current = loadSession();
  const cleared: SessionConfig = {
    ...current,
    token: "",
    orgId: "",
    userId: "",
    registrationId: "",
    profileId: "",
    snapshotId: "",
    idpSubject: "",
    email: "",
    role: "",
    pan: "",
  };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cleared));
  } catch {
    // ignore
  }
  return cleared;
}

function authHeaders(): Record<string, string> {
  const session = loadSession();
  const headers: Record<string, string> = {};
  if (session.token) {
    headers["Authorization"] = `Bearer ${session.token}`;
  } else if (isDemoAuthEnabled()) {
    if (session.orgId) headers["X-Org-Id"] = session.orgId;
    if (session.userId) headers["X-User-Id"] = session.userId;
  }
  return headers;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const session = loadSession();
  const base = resolveSessionApiBase(session.apiBase).replace(/\/+$/, "");
  const url = path.startsWith("http") ? path : `${base}${path}`;

  const mergedHeaders: Record<string, string> = {
    ...authHeaders(),
    ...(init.headers as Record<string, string> | undefined),
  };

  const res = await fetch(url, {
    ...init,
    credentials: "include",
    headers: mergedHeaders,
  });

  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try {
      const json = JSON.parse(text);
      if (json && json.detail) {
        detail = typeof json.detail === "string" ? json.detail : JSON.stringify(json.detail);
      }
    } catch {
      // keep raw text
    }
    throw new Error(`HTTP ${res.status}: ${detail || res.statusText}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

// ---------- Auth & Session Context ----------

export interface DemoTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  org_id: string;
  user_id: string;
  role: string;
  auth_mode?: string;
  provider?: string;
}

export interface IdpNonceResponse {
  nonce: string;
  expires_in: number;
  org_id: string;
  idp_provider: string;
  state: string;
  code_challenge: string;
}

export async function getCurrentUser(): Promise<AuthenticatedContext> {
  return apiFetch<AuthenticatedContext>("/api/v1/auth/me");
}

export async function logoutSession(): Promise<void> {
  try {
    await apiFetch<{ status: string }>("/api/v1/auth/logout", { method: "POST" });
  } finally {
    clearSession();
  }
}

export async function requestDemoToken(orgId: string, userId: string): Promise<DemoTokenResponse> {
  const res = await apiFetch<DemoTokenResponse>("/api/v1/auth/demo-token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ org_id: orgId, user_id: userId }),
  });
  saveSession({
    token: res.access_token,
    orgId: res.org_id,
    userId: res.user_id,
    role: res.role,
  });
  return res;
}

export async function requestIdpNonce(params: {
  orgId: string;
  provider: string;
  state: string;
  codeChallenge: string;
}): Promise<IdpNonceResponse> {
  return apiFetch<IdpNonceResponse>("/api/v1/auth/idp-nonce", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      org_id: params.orgId,
      idp_provider: params.provider,
      state: params.state,
      code_challenge: params.codeChallenge,
    }),
  });
}

export async function exchangeIdpCode(params: {
  provider: string;
  orgId: string;
  code: string;
  state: string;
  codeVerifier: string;
  redirectUri: string;
}): Promise<DemoTokenResponse> {
  const res = await apiFetch<DemoTokenResponse>("/api/v1/auth/idp-code-exchange", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      idp_provider: params.provider,
      org_id: params.orgId,
      code: params.code,
      state: params.state,
      code_verifier: params.codeVerifier,
      redirect_uri: params.redirectUri,
    }),
  });
  saveSession({
    token: res.access_token,
    orgId: res.org_id,
    userId: res.user_id,
    role: res.role,
    idpProvider: res.provider || params.provider,
  });
  return res;
}

export function generatePkceVerifier(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

export function generateOidcState(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

export async function derivePkceChallenge(verifier: string): Promise<string> {
  const data = new TextEncoder().encode(verifier);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

export function buildOidcAuthorizeUrl(params: {
  authorizeEndpoint: string;
  clientId: string;
  redirectUri: string;
  state: string;
  nonce: string;
  codeChallenge: string;
  scope?: string;
}): string {
  const url = new URL(params.authorizeEndpoint);
  const isLocalHost =
    url.hostname === "localhost" ||
    url.hostname === "127.0.0.1" ||
    url.hostname === "[::1]";
  if (url.protocol !== "https:" && !(isLocalHost && import.meta.env.MODE !== "production")) {
    throw new Error("OIDC authorize endpoint must use https://.");
  }
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", params.clientId);
  url.searchParams.set("redirect_uri", params.redirectUri);
  url.searchParams.set("scope", params.scope || "openid email profile");
  url.searchParams.set("state", params.state);
  url.searchParams.set("nonce", params.nonce);
  url.searchParams.set("code_challenge", params.codeChallenge);
  url.searchParams.set("code_challenge_method", "S256");
  return url.toString();
}

// ---------- GST Registrations ----------

export async function listRegistrations(): Promise<GstRegistration[]> {
  return apiFetch<GstRegistration[]>("/api/v1/reconciliation/gst-registrations");
}

export async function createRegistration(payload: {
  gstin: string;
  state_code: string;
  legal_name: string;
  trade_name?: string;
  registration_type?: string;
  is_primary?: boolean;
}): Promise<GstRegistration> {
  return apiFetch<GstRegistration>("/api/v1/reconciliation/gst-registrations/create", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// ---------- Outward Invoices & GSTR-1 ----------

export interface InvoiceLineItem {
  id: string;
  item_description?: string;
  hsn_sac_code?: string | null;
  quantity?: string | number | null;
  unit_price?: string | number | null;
  taxable_amount?: string | number;
  gst_rate?: string | number | null;
  cgst_amount: string | number;
  sgst_amount: string | number;
  igst_amount: string | number;
  total_amount?: string | number;
}

export interface ValidationFlag {
  code: string;
  severity: "error" | "warning" | "info";
  field?: string | null;
  message: string;
}

export interface InvoiceItem {
  id: string;
  org_id?: string;
  profile_id?: string;
  document_type?: string;
  invoice_number: string;
  invoice_date: string;
  counterparty_gstin: string | null;
  counterparty_name?: string | null;
  supply_category?: string | null;
  total_taxable_amount?: string | number;
  cgst_amount?: string | number;
  sgst_amount?: string | number;
  igst_amount?: string | number;
  total_amount: string | number;
  status: string;
  rejection_reason?: string | null;
  ocr_confidence?: number;
  is_reverse_charge?: boolean;
  line_items?: InvoiceLineItem[];
  validation_report?: {
    is_valid: boolean;
    has_critical_errors?: boolean;
    flags: ValidationFlag[];
    discrepancy_count?: number;
  };
}

export async function listInvoices(): Promise<InvoiceItem[]> {
  return apiFetch<InvoiceItem[]>("/api/v1/invoices");
}

export async function getInvoiceDetail(invoiceId: string): Promise<InvoiceItem> {
  return apiFetch<InvoiceItem>(`/api/v1/invoices/${encodeURIComponent(invoiceId)}`);
}

export async function patchInvoice(
  invoiceId: string,
  patch: {
    invoice_number?: string;
    invoice_date?: string;
    counterparty_gstin?: string | null;
    counterparty_name?: string | null;
    total_amount?: number;
  }
): Promise<InvoiceItem> {
  return apiFetch<InvoiceItem>(`/api/v1/invoices/${encodeURIComponent(invoiceId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export async function uploadInvoice(
  file: File,
  profileId?: string
): Promise<{ invoice_id: string; status: string; task_id?: string }> {
  const pid = profileId || loadSession().profileId;
  if (!pid) {
    throw new Error("No business profile ID available. Sync workspace or configure an MSME profile first.");
  }
  const fd = new FormData();
  fd.append("profile_id", pid);
  fd.append("file", file);
  return apiFetch<{ invoice_id: string; status: string; task_id?: string }>("/api/v1/invoices/upload", {
    method: "POST",
    body: fd,
  });
}

export async function approveInvoice(invoiceId: string): Promise<unknown> {
  return apiFetch(`/api/v1/invoices/${encodeURIComponent(invoiceId)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

export async function rejectInvoice(invoiceId: string, reason: string): Promise<unknown> {
  return apiFetch(`/api/v1/invoices/${encodeURIComponent(invoiceId)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
}

export async function fetchGstr1Draft(
  financialPeriod: string,
  profileId?: string
): Promise<Record<string, unknown>> {
  const qs = new URLSearchParams({ period: financialPeriod });
  if (profileId) qs.set("profile_id", profileId);
  return apiFetch<Record<string, unknown>>(`/api/v1/gst/gstr1-draft?${qs.toString()}`);
}

// ---------- GSTR-2B & IMS ----------

export interface Gstr2bUploadResponse {
  import_id: string;
  snapshot_id: string;
  version_number: number;
  recipient_gstin: string;
  return_period: string;
  financial_year: string;
  total_documents: number;
  total_itc_available: string;
  total_itc_not_available: string;
  file_hash: string;
}

export interface Gstr2bSnapshotOut {
  id: string;
  import_id?: string;
  recipient_gstin?: string;
  return_period: string;
  version_number: number;
  is_active: boolean;
  total_documents?: number;
  total_itc_available: string | number;
  total_itc_not_available: string | number;
  is_frozen_by_gstr3b?: boolean;
  created_at: string;
}

export interface Gstr2bDocumentOut {
  id: string;
  section_name?: string;
  supplier_gstin: string;
  supplier_name?: string | null;
  document_number: string;
  document_date: string;
  document_type?: string;
  taxable_value: string | number;
  central_tax?: string | number;
  state_ut_tax?: string | number;
  integrated_tax?: string | number;
  cess_amount?: string | number;
  invoice_value?: string | number;
  itc_availability: string;
  ims_action?: string | null;
  is_frozen_by_gstr3b?: boolean;
  supply_attract_reverse_charge?: boolean;
}

export function sumGstr2bDocTax(doc?: Partial<Gstr2bDocumentOut> | null): number {
  if (!doc) return 0;
  return (
    Number(doc.central_tax || 0) +
    Number(doc.state_ut_tax || 0) +
    Number(doc.integrated_tax || 0) +
    Number(doc.cess_amount || 0)
  );
}

export async function uploadGstr2b(
  registrationId: string,
  returnPeriod: string,
  file: File
): Promise<Gstr2bUploadResponse> {
  const fd = new FormData();
  fd.append("registration_id", registrationId);
  fd.append("return_period", returnPeriod);
  fd.append("file", file);
  return apiFetch<Gstr2bUploadResponse>("/api/v1/reconciliation/gstr2b/upload", {
    method: "POST",
    body: fd,
  });
}

export async function listGstr2bSnapshots(
  registrationId: string,
  returnPeriod?: string
): Promise<Gstr2bSnapshotOut[]> {
  const qs = new URLSearchParams({ registration_id: registrationId });
  if (returnPeriod) qs.set("return_period", returnPeriod);
  return apiFetch<Gstr2bSnapshotOut[]>(`/api/v1/reconciliation/gstr2b/snapshots?${qs.toString()}`);
}

export async function listGstr2bDocuments(snapshotId: string): Promise<Gstr2bDocumentOut[]> {
  const qs = new URLSearchParams({ snapshot_id: snapshotId });
  return apiFetch<Gstr2bDocumentOut[]>(`/api/v1/reconciliation/gstr2b/documents?${qs.toString()}`);
}

export async function setImsAction(
  gstr2bDocId: string,
  action: "ACCEPTED" | "REJECTED" | "PENDING",
  reason?: string
): Promise<{
  gstr2b_doc_id: string;
  current_action: string;
  previous_action?: string | null;
  action_timestamp: string;
  is_frozen_by_gstr3b: boolean;
}> {
  return apiFetch("/api/v1/reconciliation/ims/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      gstr2b_doc_id: gstr2bDocId,
      action,
      reason: reason || undefined,
    }),
  });
}

export async function recomputeGstr2b(
  registrationId: string,
  returnPeriod: string,
  snapshotId: string
): Promise<{
  new_snapshot_id: string;
  previous_snapshot_id: string;
  version_number: number;
  return_period: string;
  recipient_gstin: string;
  total_itc_available: string | number;
  total_itc_not_available: string | number;
  accepted_docs_count: number;
  pending_docs_held_count: number;
  rejected_docs_count: number;
  recomputed_at: string;
}> {
  return apiFetch("/api/v1/reconciliation/gstr2b/recompute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      registration_id: registrationId,
      return_period: returnPeriod,
      snapshot_id: snapshotId,
    }),
  });
}

// ---------- Purchase Register, Receipt Gate & Lots ----------

export interface PurchaseDocumentApi {
  id: string;
  registration_id?: string;
  document_type: string;
  document_number: string;
  document_date: string;
  financial_period: string;
  supplier_gstin: string | null;
  supplier_legal_name: string | null;
  recipient_gstin?: string | null;
  place_of_supply?: string | null;
  is_reverse_charge: boolean;
  goods_services_type?: string | null;
  receipt_status?: string | null;
  goods_receipt_date?: string | null;
  goods_receipt_note_ref?: string | null;
  delivery_challan_ref?: string | null;
  service_completion_date?: string | null;
  service_acceptance_cert_ref?: string | null;
  deemed_receipt_reason?: string | null;
  total_lots_expected?: number | null;
  lots_received_count?: number | null;
  is_last_lot_received?: boolean | null;
  total_taxable_amount: string | number;
  cgst_amount: string | number;
  sgst_amount: string | number;
  igst_amount: string | number;
  cess_amount?: string | number;
  total_invoice_value: string | number;
  available_eligible_itc?: string | number | null;
  itc_claim_status?: string | null;
  payment_status?: string | null;
  total_paid_amount?: string | number | null;
  status: string;
}

export interface LotLedgerEntryOut {
  id: string;
  purchase_doc_id: string;
  lot_number: number;
  expected_lots_count: number;
  expected_quantity: string | number;
  received_quantity: string | number;
  cumulative_received_quantity: string | number;
  lot_reference: string;
  receipt_date: string;
  goods_receipt_note_ref: string;
  carrier_name?: string | null;
  vehicle_number?: string | null;
  eway_bill_number?: string | null;
  verifier_id: string;
  verification_status: string;
  is_final_lot: boolean;
  created_at: string;
}

export async function listPurchaseDocuments(
  registrationId: string,
  financialPeriod?: string,
  limit = 100,
  offset = 0
): Promise<PurchaseDocumentApi[]> {
  const qs = new URLSearchParams({
    registration_id: registrationId,
    limit: String(limit),
    offset: String(offset),
  });
  if (financialPeriod) qs.set("financial_period", financialPeriod);
  return apiFetch<PurchaseDocumentApi[]>(
    `/api/v1/reconciliation/purchase/documents?${qs.toString()}`
  );
}

export async function createPurchaseDocument(payload: {
  registration_id: string;
  document_type?: string;
  document_number: string;
  document_date: string;
  financial_period: string;
  supplier_gstin?: string | null;
  supplier_legal_name: string;
  recipient_gstin: string;
  place_of_supply: string;
  is_reverse_charge?: boolean;
  total_taxable_amount: number;
  cgst_amount?: number;
  sgst_amount?: number;
  igst_amount?: number;
  cess_amount?: number;
  total_invoice_value?: number;
  goods_services_type?: string;
  receipt_status?: string;
  total_lots_expected?: number;
  quantity_expected?: number;
}): Promise<{ id: string; document_number: string; status: string }> {
  return apiFetch("/api/v1/reconciliation/purchase/create", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function approvePurchaseDocument(docId: string): Promise<{ status: string }> {
  return apiFetch(`/api/v1/reconciliation/purchase/${encodeURIComponent(docId)}/approve`, {
    method: "POST",
  });
}

export async function verifyPurchaseReceipt(
  docId: string,
  payload: {
    receipt_status: "RECEIVED" | "PARTIALLY_RECEIVED" | "NOT_RECEIVED" | "DEEMED_RECEIVED";
    goods_receipt_date?: string;
    goods_receipt_note_ref?: string;
    delivery_challan_ref?: string;
    service_completion_date?: string;
    service_acceptance_cert_ref?: string;
    deemed_receipt_reason?: string;
    deemed_receipt_category?: string;
  }
): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/reconciliation/purchase/${encodeURIComponent(docId)}/verify-receipt`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listPurchaseLots(docId: string): Promise<LotLedgerEntryOut[]> {
  return apiFetch<LotLedgerEntryOut[]>(
    `/api/v1/reconciliation/purchase/${encodeURIComponent(docId)}/lots`
  );
}

export async function recordPurchaseLot(
  docId: string,
  payload: {
    lot_number: number;
    expected_lots_count?: number;
    received_quantity: number;
    lot_reference: string;
    receipt_date: string;
    goods_receipt_note_ref: string;
    carrier_name?: string;
    vehicle_number?: string;
    eway_bill_number?: string;
  }
): Promise<LotLedgerEntryOut> {
  return apiFetch<LotLedgerEntryOut>(
    `/api/v1/reconciliation/purchase/${encodeURIComponent(docId)}/record-lot`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );
}

export async function recordPurchasePayment(payload: {
  payment_reference: string;
  payment_date: string;
  payment_mode: string;
  total_amount_paid: number;
  allocations: Array<{
    purchase_doc_id: string;
    allocated_taxable_amount: number;
    allocated_tax_amount: number;
    allocated_total_amount: number;
  }>;
}): Promise<Record<string, unknown>> {
  return apiFetch("/api/v1/reconciliation/purchase/payment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// ---------- Reconciliation, Exceptions & Audit Reports ----------

export interface ComponentTaxAmount {
  cgst: string | number;
  sgst: string | number;
  igst: string | number;
  cess: string | number;
  total: string | number;
}

export interface Table4Summary {
  disclaimer?: string;
  return_period: string;
  recipient_gstin: string;
  itc_available_import_goods: string | number;
  itc_available_import_services: string | number;
  itc_available_inward_reverse_charge: string | number;
  itc_available_isd: string | number;
  itc_available_all_other: string | number;
  total_itc_available: string | number;
  itc_reversed_rule_38_42_43: string | number;
  itc_reversed_rule_37_180_days: string | number;
  itc_reversed_rule_37a: string | number;
  itc_reversed_sec_16_2_b_receipt: string | number;
  itc_reversed_other: string | number;
  total_itc_reversed_reclaimable_4b2: string | number;
  total_itc_reversed: string | number;
  net_itc_available: string | number;
  itc_reclaimed_earlier_reversal_4d1: string | number;
  ineligible_sec_16_4_and_pos_restrictions_4d2: string | number;
  components_4a?: Record<string, ComponentTaxAmount> | null;
  components_4b?: Record<string, ComponentTaxAmount> | null;
  components_4c_net?: ComponentTaxAmount | null;
  components_4d?: Record<string, ComponentTaxAmount> | null;
  reversals_lineage?: Array<Record<string, unknown>>;
  unbooked_gstr2b_documents?: Array<Record<string, unknown>>;
}

export interface ReconciliationSummaryOut {
  run_id: string;
  return_period: string;
  matched_count: number;
  variance_count: number;
  missing_in_gstr2b_count: number;
  missing_in_books_count: number;
  matches: Array<{
    purchase_doc_id: string;
    gstr2b_doc_id: string;
    match_pass: number;
    match_score: string | number;
    is_exact: boolean;
    taxable_difference: string | number;
    tax_difference: string | number;
    total_difference: string | number;
    match_evidence: Record<string, unknown>;
  }>;
  variances: Array<{
    variance_category: string;
    purchase_doc_id?: string | null;
    gstr2b_doc_id?: string | null;
    difference_details: Record<string, unknown>;
  }>;
}

export interface ReconciliationExceptionOut {
  exception_id: string;
  category: string;
  document_number: string;
  document_date?: string | null;
  supplier_gstin?: string | null;
  supplier_name?: string | null;
  taxable_value: string | number;
  cgst_amount: string | number;
  sgst_amount: string | number;
  igst_amount: string | number;
  cess_amount: string | number;
  total_tax: string | number;
  actionable_explanation: string;
  statutory_citation: string;
  suggested_action: string;
  statutory_deadline?: string | null;
  resolution_status: string;
  source_reference_id?: string | null;
  created_at: string;
}

export interface UnbookedCandidateOut {
  id: string;
  snapshot_id: string;
  gstr2b_doc_id: string;
  supplier_gstin: string;
  supplier_name?: string | null;
  document_number: string;
  document_date: string;
  taxable_value: string | number;
  cgst_amount: string | number;
  sgst_amount: string | number;
  igst_amount: string | number;
  cess_amount: string | number;
  total_tax: string | number;
  receipt_status: string;
  reconciliation_status: string;
  is_reversed_under_4b2: boolean;
  is_reclaimed: boolean;
  linked_purchase_doc_id?: string | null;
  created_at: string;
}

export interface UnifiedFinancialReportOut {
  registration_id: string;
  return_period: string;
  generated_at: string;
  gstr2b_total: string | number;
  purchase_books_matched: string | number;
  unbooked_2b_variance: string | number;
  prior_period_reclaims: string | number;
  table_4a_gross: string | number;
  table_4b1_permanent_reversals: string | number;
  table_4b2_reclaimable_reversals: string | number;
  table_4b2_receipt_reversals: string | number;
  table_4c_net_eligible_itc: string | number;
  table_4d1_reclaimed_disclosure: string | number;
  table_4d2_ineligible_disclosure: string | number;
  human_approved_itc: string | number;
  outstanding_exceptions_count: number;
  exceptions_by_category: Record<string, number>;
  tax_head_summary: Record<string, Record<string, string | number>>;
  supplier_wise_itc: Array<Record<string, unknown>>;
  reversals_lineage?: Array<Record<string, unknown>>;
  unbooked_gstr2b_documents?: Array<Record<string, unknown>>;
  itc_accounting_states?: Record<string, unknown>;
  electronic_credit_reversal_reclaimed_statement?: {
    opening_reversal_balance: string | number;
    current_period_reversals_table_4b2: string | number;
    current_period_reclaims_table_4d1: string | number;
    closing_reversal_balance: string | number;
    [key: string]: unknown;
  };
  rule_37_interest_dual_assessment?: {
    target_invoice?: string;
    primary_statutory_basis_rule_88b_3?: {
      interest_liability: string | number;
      status: string;
    };
    departmental_assessment_view_sec_50_1?: {
      basis_from_181st_day?: { interest_exposure: string | number; days?: number };
      basis_from_availment_date?: { interest_exposure: string | number; days?: number };
    };
    [key: string]: unknown;
  };
}

export interface ItcLedgerSummaryOut {
  total_availed: ComponentTaxAmount;
  total_reversed_permanent: ComponentTaxAmount;
  total_reversed_temporary: ComponentTaxAmount;
  total_reavailed: ComponentTaxAmount;
  net_active_itc_balance: ComponentTaxAmount;
  total_interest_assessed: string | number;
  entries_count: number;
}

export async function runReconciliation(payload: {
  registration_id: string;
  return_period: string;
  snapshot_id: string;
  taxable_tolerance?: number;
  tax_tolerance?: number;
  total_tolerance?: number;
}): Promise<ReconciliationSummaryOut> {
  return apiFetch<ReconciliationSummaryOut>("/api/v1/reconciliation/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function resolveVariance(payload: {
  variance_id?: string;
  purchase_doc_id?: string;
  gstr2b_doc_id?: string;
  resolution_action: string;
  resolution_notes: string;
}): Promise<{ id: string; resolution_action: string; resolved_by: string; created_at: string }> {
  return apiFetch("/api/v1/reconciliation/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listUnbookedCandidates(snapshotId: string): Promise<UnbookedCandidateOut[]> {
  const qs = new URLSearchParams({ snapshot_id: snapshotId });
  return apiFetch<UnbookedCandidateOut[]>(
    `/api/v1/reconciliation/unbooked-candidates?${qs.toString()}`
  );
}

export async function bookUnbookedCandidate(
  candidateId: string,
  payload: {
    registration_id: string;
    financial_period: string;
    purchase_ledger_code?: string;
    notes?: string;
  }
): Promise<Record<string, unknown>> {
  return apiFetch(
    `/api/v1/reconciliation/unbooked-candidates/${encodeURIComponent(candidateId)}/book-entry`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );
}

export async function getTable4Summary(
  registrationId: string,
  returnPeriod: string,
  snapshotId: string
): Promise<Table4Summary> {
  const qs = new URLSearchParams({
    registration_id: registrationId,
    return_period: returnPeriod,
    snapshot_id: snapshotId,
  });
  return apiFetch<Table4Summary>(
    `/api/v1/reconciliation/gstr3b-table4-working-summary?${qs.toString()}`
  );
}

export async function listExceptions(
  registrationId: string,
  returnPeriod: string,
  snapshotId?: string
): Promise<ReconciliationExceptionOut[]> {
  const qs = new URLSearchParams({
    registration_id: registrationId,
    return_period: returnPeriod,
  });
  if (snapshotId) qs.set("snapshot_id", snapshotId);
  return apiFetch<ReconciliationExceptionOut[]>(
    `/api/v1/reconciliation/exceptions?${qs.toString()}`
  );
}

export async function getUnifiedFinancialReport(
  registrationId: string,
  returnPeriod: string,
  snapshotId?: string
): Promise<UnifiedFinancialReportOut> {
  const qs = new URLSearchParams({
    registration_id: registrationId,
    return_period: returnPeriod,
  });
  if (snapshotId) qs.set("snapshot_id", snapshotId);
  return apiFetch<UnifiedFinancialReportOut>(
    `/api/v1/reconciliation/financial-reports/unified?${qs.toString()}`
  );
}

export async function getItcLedgerSummary(taxPeriod?: string): Promise<ItcLedgerSummaryOut> {
  const qs = new URLSearchParams();
  if (taxPeriod) qs.set("tax_period", taxPeriod);
  return apiFetch<ItcLedgerSummaryOut>(
    `/api/v1/reconciliation/itc-ledger/summary${qs.toString() ? `?${qs.toString()}` : ""}`
  );
}

export async function exportReconciliationAuditReport(
  registrationId: string,
  returnPeriod: string,
  snapshotId: string
): Promise<Record<string, unknown>> {
  const qs = new URLSearchParams({
    registration_id: registrationId,
    return_period: returnPeriod,
    snapshot_id: snapshotId,
  });
  return apiFetch<Record<string, unknown>>(
    `/api/v1/reconciliation/export/audit-report?${qs.toString()}`
  );
}

// ---------- GSTR-3B Offset & Filing ----------

export interface Gstr3bRecord {
  id?: string;
  filing_id?: string;
  org_id?: string;
  registration_id?: string;
  gstin?: string;
  tax_period: string;
  filing_status?: string;
  status?: string;
  filing_mode?: string;
  is_live_portal_filing?: boolean;
  gsp_reference_id?: string | null;
  table_3_1?: Record<string, any>;
  table_4?: Record<string, any>;
  offset?: Record<string, any>;
  table_3_1_data?: Record<string, any>;
  table_4_data?: Record<string, any>;
  offset_data?: Record<string, any>;
  arn?: string | null;
  cin?: string | null;
  filing_date?: string | null;
  maker_remarks?: string | null;
  checker_remarks?: string | null;
  checker_notes?: string | null;
}

export async function getGstr3bStatus(
  registrationId: string,
  taxPeriod: string
): Promise<Gstr3bRecord> {
  const qs = new URLSearchParams({
    registration_id: registrationId,
    tax_period: taxPeriod,
  });
  return apiFetch<Gstr3bRecord>(`/api/v1/gstr3b/status?${qs.toString()}`);
}

export async function prepareGstr3b(
  registrationId: string,
  taxPeriod: string,
  snapshotId?: string
): Promise<Gstr3bRecord> {
  return apiFetch<Gstr3bRecord>("/api/v1/gstr3b/prepare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      registration_id: registrationId,
      tax_period: taxPeriod,
      ...(snapshotId ? { snapshot_id: snapshotId } : {}),
    }),
  });
}

export async function submitGstr3bForReview(
  filingId: string,
  remarks?: string
): Promise<{ status: string; filing_id: string }> {
  return apiFetch(`/api/v1/gstr3b/${encodeURIComponent(filingId)}/submit-review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      remarks: remarks || "Submitted for CA / Checker statutory review.",
    }),
  });
}

export async function reviewGstr3bDecision(
  filingId: string,
  decision: "APPROVED" | "REJECTED",
  remarks: string
): Promise<{ status: string; filing_id: string }> {
  return apiFetch(`/api/v1/gstr3b/${encodeURIComponent(filingId)}/review-decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, remarks }),
  });
}

export async function fileGstr3bToSandbox(filingId: string): Promise<Gstr3bRecord> {
  return apiFetch<Gstr3bRecord>(`/api/v1/gstr3b/${encodeURIComponent(filingId)}/file`, {
    method: "POST",
  });
}

// ---------- MSME Credit Builder ----------

export interface MonthlyCashFlowTrend {
  month: string;
  inflow: string | number;
  outflow: string | number;
  net_cash_flow: string | number;
  transaction_count: number;
}

export interface CategoryBreakdownItem {
  category: string;
  amount: string | number;
  percentage: string | number;
}

export interface FinancialSummaryOut {
  total_revenue: string;
  total_expenses: string;
  net_profit_proxy: string;
  net_margin_pct: string;
  monthly_trends: MonthlyCashFlowTrend[];
  expense_categories: CategoryBreakdownItem[];
  revenue_categories: CategoryBreakdownItem[];
}

export interface ReadinessFactor {
  name: string;
  score: number;
  max_score: number;
  rating: string;
  description: string;
}

export interface ReadinessScoreOut {
  score: number;
  rating: string;
  dscr_proxy: string;
  cash_flow_stability: string;
  gst_compliance_score: number;
  factors: ReadinessFactor[];
  disclaimer: string;
}

export interface LoanDossierOut {
  legal_name: string;
  pan: string;
  gstin?: string | null;
  scorecard: ReadinessScoreOut;
  summary: FinancialSummaryOut;
  recommended_loan_products: string[];
  generated_at: string;
  disclaimer: string;
}

export async function uploadBankStatement(csvText: string): Promise<{
  transactions_imported: number;
  total_credits: string;
  total_debits: string;
}> {
  const fd = new FormData();
  fd.append("csv_text", csvText);
  return apiFetch("/api/v1/credit-builder/statements/upload", {
    method: "POST",
    body: fd,
  });
}

export async function getCreditSummary(): Promise<FinancialSummaryOut> {
  return apiFetch<FinancialSummaryOut>("/api/v1/credit-builder/summary");
}

export async function getReadinessScore(): Promise<ReadinessScoreOut> {
  return apiFetch<ReadinessScoreOut>("/api/v1/credit-builder/readiness-score");
}

export async function getLoanDossier(): Promise<LoanDossierOut> {
  return apiFetch<LoanDossierOut>("/api/v1/credit-builder/loan-dossier");
}

// ---------- MSME Scheme Navigator ----------

export interface BusinessProfile {
  id: string;
  org_id: string;
  business_type: string;
  industry_sector: string;
  turnover_bracket?: string | null;
  annual_turnover: string | number;
  udyam_number?: string | null;
  vintage_years?: number | null;
  social_category?: string | null;
  has_udyam?: boolean;
}

export interface SchemeMatch {
  scheme_id: string;
  scheme_code: string;
  scheme_name: string;
  ministry: string;
  category: string;
  match_score: string | number;
  status: string;
  evaluated_criteria: Array<{
    rule_key: string;
    description: string;
    passed: boolean;
    expected: string;
    actual?: string;
  }>;
  benefits_summary: string;
  max_subsidy_amount?: string | null;
}

export interface SchemeChecklist {
  scheme_code: string;
  name: string;
  ministry: string;
  category: string;
  eligibility_summary: string;
  required_documents: string[];
  application_steps: string[];
  portal_url: string;
  official_guidelines_url?: string | null;
}

export async function getBusinessProfile(): Promise<BusinessProfile> {
  return apiFetch<BusinessProfile>("/api/v1/scheme-navigator/profile");
}

export async function updateBusinessProfile(
  profile: Partial<BusinessProfile>
): Promise<BusinessProfile> {
  return apiFetch<BusinessProfile>("/api/v1/scheme-navigator/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
}

export async function evaluateSchemes(
  profileOverride?: Partial<BusinessProfile>
): Promise<SchemeMatch[]> {
  return apiFetch<SchemeMatch[]>("/api/v1/scheme-navigator/evaluate", {
    method: "POST",
    ...(profileOverride
      ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(profileOverride),
        }
      : {}),
  });
}

export async function getSchemeChecklist(schemeCode: string): Promise<SchemeChecklist> {
  return apiFetch<SchemeChecklist>(
    `/api/v1/scheme-navigator/checklist/${encodeURIComponent(schemeCode)}`
  );
}

// ---------- AI Financial Advisor ----------

export interface ChatResponse {
  reply: string;
  grounding_applied: boolean;
  grounding_context?: Record<string, any>;
  model_used: string;
  timestamp: string;
}

export interface QuickInsight {
  id: string;
  category: string;
  title: string;
  summary: string;
  statutory_reference?: string | null;
  severity: "info" | "warning" | "critical";
  action_link?: string | null;
}

export async function getQuickInsights(): Promise<QuickInsight[]> {
  return apiFetch<QuickInsight[]>("/api/v1/ai/quick-insights");
}

export async function chatWithAdvisor(
  messages: Array<{ role: "user" | "assistant"; content: string }>,
  includeGstGrounding = true
): Promise<ChatResponse> {
  return apiFetch<ChatResponse>("/api/v1/ai/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, include_gst_grounding: includeGstGrounding }),
  });
}
