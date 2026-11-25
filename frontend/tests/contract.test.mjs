import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const apiTsPath = path.resolve(import.meta.dirname, "../src/api.ts");
const gstr3bViewPath = path.resolve(import.meta.dirname, "../src/pages/Gstr3bFilingView.tsx");
const appTsxPath = path.resolve(import.meta.dirname, "../src/App.tsx");
const backendGstr3bPath = path.resolve(import.meta.dirname, "../../backend/app/modules/gstr3b/api.py");

test("frontend api.ts matches backend GSTR-3B routes without stale endpoints", () => {
  const apiSrc = fs.readFileSync(apiTsPath, "utf8");
  const backendSrc = fs.readFileSync(backendGstr3bPath, "utf8");

  assert.match(apiSrc, /\/api\/v1\/gstr3b\/\$\{filingId\}\/submit-review/);
  assert.match(apiSrc, /\/api\/v1\/gstr3b\/\$\{filingId\}\/review-decision/);
  assert.match(apiSrc, /\/api\/v1\/gstr3b\/\$\{filingId\}\/file`/);
  assert.match(apiSrc, /\/api\/v1\/gstr3b\/status\?/);
  assert.doesNotMatch(apiSrc, /\/file-sandbox/);
  assert.doesNotMatch(apiSrc, /\/review`/);

  assert.match(backendSrc, /@router\.post\("\/\{filing_id\}\/submit-review"\)/);
  assert.match(backendSrc, /@router\.post\("\/\{filing_id\}\/review-decision"\)/);
  assert.match(backendSrc, /@router\.post\("\/\{filing_id\}\/file"/);
});

test("frontend api.ts includes OIDC PKCE nonce/exchange with credentials include", () => {
  const apiSrc = fs.readFileSync(apiTsPath, "utf8");
  assert.match(apiSrc, /\/api\/v1\/auth\/idp-nonce/);
  assert.match(apiSrc, /\/api\/v1\/auth\/idp-exchange/);
  assert.match(apiSrc, /credentials:\s*"include"/);
  assert.match(apiSrc, /isDemoAuthEnabled/);
});

test("Gstr3bFilingView defaults to YYYY-MM and App.tsx has no hardcoded PAN/GSTIN", () => {
  const viewSrc = fs.readFileSync(gstr3bViewPath, "utf8");
  const appSrc = fs.readFileSync(appTsxPath, "utf8");

  assert.match(viewSrc, /"2026-11"/);
  assert.doesNotMatch(viewSrc, /"102025"/);
  assert.doesNotMatch(appSrc, /27AABCB1234M1ZT/);
  assert.doesNotMatch(appSrc, /handleUploadSimulate/);
});
