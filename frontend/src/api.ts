const env = (import.meta as any).env ?? {};

export const API_BASE_URL: string = (env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export interface StoredSession {
  token: string;
  userId: string;
  orgId: string;
  registrationId: string;
  snapshotId: string;
  returnPeriod: string;
}

const KEYS = {
  token: "myca_token",
  userId: "myca_user_id",
  orgId: "myca_org_id",
  registrationId: "myca_registration_id",
  snapshotId: "myca_snapshot_id",
  returnPeriod: "myca_return_period",
} as const;

export function getStoredSession(): StoredSession {
  return {
    token: localStorage.getItem(KEYS.token) ?? "",
    userId: localStorage.getItem(KEYS.userId) ?? env.VITE_USER_ID ?? "",
    orgId: localStorage.getItem(KEYS.orgId) ?? env.VITE_ORG_ID ?? "",
    registrationId: localStorage.getItem(KEYS.registrationId) ?? env.VITE_REGISTRATION_ID ?? "",
    snapshotId: localStorage.getItem(KEYS.snapshotId) ?? "",
    returnPeriod: localStorage.getItem(KEYS.returnPeriod) ?? "2026-11",
  };
}

export function saveSession(session: Partial<StoredSession>): void {
  if (session.token !== undefined) localStorage.setItem(KEYS.token, session.token);
  if (session.userId !== undefined) localStorage.setItem(KEYS.userId, session.userId);
  if (session.orgId !== undefined) localStorage.setItem(KEYS.orgId, session.orgId);
  if (session.registrationId !== undefined) localStorage.setItem(KEYS.registrationId, session.registrationId);
  if (session.snapshotId !== undefined) localStorage.setItem(KEYS.snapshotId, session.snapshotId);
  if (session.returnPeriod !== undefined) localStorage.setItem(KEYS.returnPeriod, session.returnPeriod);
}

export function clearSession(): void {
  Object.values(KEYS).forEach((key) => localStorage.removeItem(key));
}

export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const session = getStoredSession();
  if (session.token) {
    return {
      Authorization: `Bearer ${session.token}`,
      ...(session.userId ? { "X-User-Id": session.userId } : {}),
      ...(session.orgId ? { "X-Org-Id": session.orgId } : {}),
      ...extra,
    };
  }
  return {
    ...(session.userId ? { "X-User-Id": session.userId } : {}),
    ...(session.orgId ? { "X-Org-Id": session.orgId } : {}),
    ...extra,
  };
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) {
      return body.detail.map((d: any) => d?.msg ?? JSON.stringify(d)).join("; ");
    }
    return `Request failed with status ${response.status}.`;
  } catch {
    return `Request failed with status ${response.status}.`;
  }
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: authHeaders(options.headers as Record<string, string> | undefined),
  });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return (await response.json()) as T;
}

export interface DemoTokenResponse {
  access_token: string;
  token_type: string;
  user_id: string;
  org_id: string;
  expires_in: number;
}

export async function requestDemoToken(userId: string, orgId: string): Promise<DemoTokenResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/auth/demo-token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, org_id: orgId }),
  });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return (await response.json()) as DemoTokenResponse;
}

export interface PurchaseDocumentApi {
  id: string;
  registration_id: string;
  document_type: string;
  document_number: string;
  document_date: string;
  financial_period: string;
  supplier_gstin: string;
  supplier_legal_name: string;
  recipient_gstin: string;
  place_of_supply: string;
  is_reverse_charge: boolean;
  supply_category?: string | null;
  total_taxable_amount?: string | null;
  cgst_amount?: string | null;
  sgst_amount?: string | null;
  igst_amount?: string | null;
  cess_amount?: string | null;
  total_invoice_value?: string | null;
  goods_services_type: string;
  status: string;
  created_at: string;
}

export function listPurchaseDocuments(registrationId: string): Promise<PurchaseDocumentApi[]> {
  const params = new URLSearchParams({ registration_id: registrationId });
  return apiFetch<PurchaseDocumentApi[]>(`/api/v1/reconciliation/purchase/documents?${params.toString()}`);
}

export interface ReconciliationRunConfig {
  registration_id: string;
  return_period: string;
  snapshot_id: string;
  taxable_tolerance: string;
  tax_tolerance: string;
  total_tolerance: string;
}

export interface ReconciliationSummary {
  run_id: string;
  return_period: string;
  matched_count: number;
  variance_count: number;
  missing_in_gstr2b_count: number;
  missing_in_books_count: number;
  matches: any[];
  variances: any[];
}

export function runReconciliation(config: ReconciliationRunConfig): Promise<ReconciliationSummary> {
  return apiFetch<ReconciliationSummary>("/api/v1/reconciliation/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}

export interface Table4Summary {
  return_period: string;
  recipient_gstin: string;
  total_itc_available: string;
  total_itc_reversed: string;
  net_itc_available: string;
  itc_reclaimed_earlier_reversal_4d1: string;
  ineligible_sec_16_4_and_pos_restrictions_4d2: string;
  subtotal_legally_eligible: string;
  unbooked_gstr2b_documents: any[];
  reversals_lineage: any[];
}

export function getTable4Summary(registrationId: string, returnPeriod: string, snapshotId: string): Promise<Table4Summary> {
  const params = new URLSearchParams({
    registration_id: registrationId,
    return_period: returnPeriod,
    snapshot_id: snapshotId,
  });
  return apiFetch<Table4Summary>(`/api/v1/reconciliation/gstr3b-table4-working-summary?${params.toString()}`);
}

export interface ItcLedgerSummary {
  total_availed: any;
  total_reversed_permanent: any;
  total_reversed_temporary: any;
  total_reavailed: any;
  net_active_itc_balance: any;
  total_interest_assessed: string;
  entries_count: number;
}

export function getItcLedgerSummary(): Promise<ItcLedgerSummary> {
  return apiFetch<ItcLedgerSummary>("/api/v1/reconciliation/itc-ledger/summary");
}
