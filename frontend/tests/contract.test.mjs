import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, "..");
const srcDir = path.join(rootDir, "src");

const apiSource = fs.readFileSync(path.join(srcDir, "api.ts"), "utf8");
const appSource = fs.readFileSync(path.join(srcDir, "App.tsx"), "utf8");
const purchaseSource = fs.readFileSync(path.join(srcDir, "pages", "PurchaseRegister.tsx"), "utf8");
const reconSource = fs.readFileSync(path.join(srcDir, "pages", "ReconciliationWorkbench.tsx"), "utf8");
const riskSource = fs.readFileSync(path.join(srcDir, "pages", "ItcRiskDashboard.tsx"), "utf8");
const gstr2bSource = fs.readFileSync(path.join(srcDir, "pages", "Gstr2bUpload.tsx"), "utf8");
const gstr3bSource = fs.readFileSync(path.join(srcDir, "pages", "Gstr3bFilingView.tsx"), "utf8");
const creditSource = fs.readFileSync(path.join(srcDir, "pages", "CreditBuilder.tsx"), "utf8");
const schemeSource = fs.readFileSync(path.join(srcDir, "pages", "SchemeNavigator.tsx"), "utf8");
const aiSource = fs.readFileSync(path.join(srcDir, "pages", "AiAdvisor.tsx"), "utf8");
const nginxSource = fs.readFileSync(path.join(rootDir, "nginx.conf"), "utf8");

test("OIDC flow binds org_id, idp_provider, state, and PKCE S256", () => {
  assert.match(apiSource, /response_type",\s*"code"/);
  assert.match(apiSource, /code_challenge",\s*params\.codeChallenge/);
  assert.match(apiSource, /code_challenge_method",\s*"S256"/);
  assert.match(apiSource, /org_id:\s*params\.orgId/);
  assert.match(apiSource, /idp_provider:\s*params\.provider/);
  assert.match(apiSource, /\/api\/v1\/auth\/idp-code-exchange/);
  assert.match(apiSource, /authorize endpoint must use https:\/\//i);
  assert.doesNotMatch(apiSource, /response_type",\s*"id_token"/);
});

test("Production gates disable demo tokens & use same-origin production API default", () => {
  assert.match(apiSource, /import\.meta\.env\.PROD\s*\?\s*""\s*:\s*"http:\/\/127\.0\.0\.1:8000"/);
  assert.match(apiSource, /if \(mode === "production"\)\s*\{\s*return false;/);
  assert.match(appSource, /demoAllowed &&/);
  assert.match(apiSource, /\/api\/v1\/auth\/me/);
  assert.match(apiSource, /\/api\/v1\/auth\/logout/);
  assert.doesNotMatch(apiSource, /promote-to-purchase/);
  assert.doesNotMatch(apiSource, /body:\s*JSON\.stringify\(profileOverride\s*\|\|\s*\{\s*\}\)/);
});

test("Outward invoices, GSTR-1, and AI routes align strictly with backend", () => {
  assert.match(appSource, /counterparty_gstin/);
  assert.match(appSource, /counterparty_name/);
  assert.match(appSource, /validation_report\.flags/);
  assert.match(appSource, /pending_review/);
  assert.match(appSource, /rejectInvoice/);
  assert.match(appSource, /patchInvoice/);
  assert.doesNotMatch(appSource, /validation_report\.errors/);
  assert.match(apiSource, /\/api\/v1\/gst\/gstr1-draft/);
  assert.match(apiSource, /\/api\/v1\/ai\/quick-insights/);
  assert.match(apiSource, /\/api\/v1\/ai\/chat/);
  assert.doesNotMatch(apiSource, /\/returns\/gstr1\/draft/);
  assert.doesNotMatch(apiSource, /\/ai-assistant\//);
  assert.match(aiSource, /res\.reply/);
});

test("Compliance pages have no hardcoded demo fixtures or fabricated metrics", () => {
  for (const [name, src] of [
    ["PurchaseRegister", purchaseSource],
    ["ReconciliationWorkbench", reconSource],
    ["ItcRiskDashboard", riskSource],
  ]) {
    assert.doesNotMatch(src, /Sai Motors|Saffron Caterers|Alpha Heavy|Haryana Logistics|Zenith Tech/i, name);
    assert.doesNotMatch(src, /4,82,500|3,36,600|17,250\.00/, name);
  }
  assert.doesNotMatch(creditSource, /score\s*\|\|\s*680/);
  assert.doesNotMatch(creditSource, /dscr_proxy\s*\|\|\s*"2\.10"/);
  assert.doesNotMatch(schemeSource, /vintage_years:\s*3\b/);
  assert.doesNotMatch(gstr2bSource, /27AABCB1234M1ZT/);
  assert.doesNotMatch(gstr2bSource, /50MB/);
  assert.doesNotMatch(gstr2bSource, /\.zip/);
});

test("All compliance, credit, and analytics modules match exact backend DTO fields", () => {
  assert.match(purchaseSource, /createPurchaseDocument/);
  assert.match(purchaseSource, /verifyPurchaseReceipt/);
  assert.match(purchaseSource, /recordPurchaseLot/);
  assert.match(purchaseSource, /recordPurchasePayment/);
  assert.match(reconSource, /TAX_MISMATCH/);
  assert.match(reconSource, /AMENDED_BY_SUPPLIER/);
  assert.match(reconSource, /sumGstr2bDocTax/);
  assert.match(riskSource, /components_4a/);
  assert.match(riskSource, /components_4c_net/);
  assert.match(riskSource, /opening_reversal_balance/);
  assert.match(riskSource, /primary_statutory_basis_rule_88b_3/);
  assert.match(apiSource, /tax_period/);
  assert.match(gstr2bSource, /recomputeGstr2b\(registrationId, returnPeriod, activeSnapshotId\)/);
  assert.match(gstr3bSource, /d_inward_rcm/);
  assert.match(gstr3bSource, /a_itc_available/);
  assert.match(gstr3bSource, /SANDBOX SIMULATION|is_live_portal_filing/);
  assert.match(apiSource, /registration_id:\s*registrationId,\s*tax_period:\s*taxPeriod/);
  assert.match(apiSource, /decision,\s*remarks/);
  assert.match(apiSource, /fd\.append\("registration_id",\s*registrationId\)/);
  assert.match(apiSource, /fd\.append\("profile_id",\s*pid\)/);
  assert.match(schemeSource, /vintageYears/);
  assert.match(schemeSource, /socialCategory/);
  assert.match(schemeSource, /hasUdyam/);
  assert.match(creditSource, /from "recharts"/);
  assert.match(creditSource, /t\.inflow/);
  assert.match(creditSource, /t\.outflow/);
  assert.doesNotMatch(aiSource, /dangerouslySetInnerHTML/);
  assert.match(nginxSource, /client_max_body_size\s+30m/);
});
