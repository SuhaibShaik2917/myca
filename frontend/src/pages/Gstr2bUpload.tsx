import React, { useState, useEffect, useCallback } from "react";
import {
  UploadCloud,
  CheckCircle2,
  XCircle,
  Clock,
  FileJson,
  ShieldAlert,
  AlertCircle,
  Lock,
  RefreshCw,
} from "lucide-react";
import {
  loadSession,
  saveSession,
  uploadGstr2b,
  listGstr2bSnapshots,
  listGstr2bDocuments,
  setImsAction,
  recomputeGstr2b,
  sumGstr2bDocTax,
  Gstr2bSnapshotOut,
  Gstr2bDocumentOut,
  GstRegistration,
} from "../api";

interface Props {
  registrations?: GstRegistration[];
}

export const Gstr2bUpload: React.FC<Props> = ({ registrations = [] }) => {
  const initialSession = loadSession();
  const [registrationId, setRegistrationId] = useState(
    initialSession.registrationId || registrations[0]?.id || ""
  );
  const [returnPeriod, setReturnPeriod] = useState(initialSession.returnPeriod || "2026-11");
  const [snapshots, setSnapshots] = useState<Gstr2bSnapshotOut[]>([]);
  const [activeSnapshotId, setActiveSnapshotId] = useState(initialSession.snapshotId || "");
  const [docs, setDocs] = useState<Gstr2bDocumentOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadResult, setUploadResult] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const selectedReg = registrations.find((r) => r.id === registrationId);

  const loadSnapshotsAndDocs = useCallback(
    async (regId: string, period: string, preferredSnapId?: string) => {
      if (!regId) {
        setSnapshots([]);
        setDocs([]);
        return;
      }
      setLoading(true);
      setErrorMsg(null);
      try {
        const snaps = await listGstr2bSnapshots(regId, period);
        setSnapshots(snaps);
        const chosen =
          (preferredSnapId && snaps.find((s) => s.id === preferredSnapId)) ||
          snaps.find((s) => s.is_active) ||
          snaps[0];
        if (chosen) {
          setActiveSnapshotId(chosen.id);
          saveSession({ registrationId: regId, snapshotId: chosen.id, returnPeriod: period });
          const docList = await listGstr2bDocuments(chosen.id);
          setDocs(docList);
        } else {
          setActiveSnapshotId("");
          saveSession({ registrationId: regId, snapshotId: "", returnPeriod: period });
          setDocs([]);
        }
      } catch (err: any) {
        setErrorMsg(err?.message || "Failed to load GSTR-2B snapshots from backend.");
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    const sess = loadSession();
    const targetReg = registrationId || sess.registrationId || registrations[0]?.id || "";
    if (targetReg && targetReg !== registrationId) {
      setRegistrationId(targetReg);
    }
    if (targetReg) {
      loadSnapshotsAndDocs(targetReg, returnPeriod, sess.snapshotId);
    }
  }, [registrationId, returnPeriod, registrations, loadSnapshotsAndDocs]);

  const handleFileUpload = async (file: File) => {
    if (!registrationId) {
      setErrorMsg("Select or register a GSTIN registration before uploading GSTR-2B.");
      return;
    }
    setErrorMsg(null);
    setUploadResult(null);
    setLoading(true);
    try {
      const res = await uploadGstr2b(registrationId, returnPeriod, file);
      saveSession({
        registrationId,
        snapshotId: res.snapshot_id,
        returnPeriod: res.return_period,
      });
      setActiveSnapshotId(res.snapshot_id);
      setUploadResult(
        `Parsed ${file.name} -> Version v${res.version_number} (${res.total_documents} docs, Eligible ITC ₹${Number(
          res.total_itc_available || 0
        ).toLocaleString("en-IN", { minimumFractionDigits: 2 })})`
      );
      await loadSnapshotsAndDocs(registrationId, returnPeriod, res.snapshot_id);
    } catch (err: any) {
      setErrorMsg(err?.message || "GSTR-2B upload failed.");
    } finally {
      setLoading(false);
    }
  };

  const handleImsUpdate = async (docId: string, action: "ACCEPTED" | "REJECTED" | "PENDING") => {
    setErrorMsg(null);
    try {
      await setImsAction(docId, action);
      setDocs((prev) =>
        prev.map((d) => (d.id === docId ? { ...d, ims_action: action } : d))
      );
      setUploadResult(`IMS status updated to ${action}. Recompute GSTR-2B to freeze snapshot changes.`);
    } catch (err: any) {
      setErrorMsg(err?.message || "Failed to update IMS action.");
    }
  };

  const handleRecompute = async () => {
    if (!activeSnapshotId || !registrationId) return;
    setErrorMsg(null);
    setLoading(true);
    try {
      const res = await recomputeGstr2b(registrationId, returnPeriod, activeSnapshotId);
      setUploadResult(
        `Recomputed snapshot v${res.version_number}: Accepted ${res.accepted_docs_count}, Rejected ${res.rejected_docs_count}, Pending ${res.pending_docs_held_count}.`
      );
      await loadSnapshotsAndDocs(registrationId, returnPeriod, res.new_snapshot_id);
    } catch (err: any) {
      setErrorMsg(err?.message || "Snapshot recompute failed.");
    } finally {
      setLoading(false);
    }
  };

  const currentSnapshot = snapshots.find((s) => s.id === activeSnapshotId);

  return (
    <div className="space-y-6">
      {/* Header Controls */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
            <FileJson className="w-5 h-5 text-indigo-600" />
            GSTR-2B Portal Statement &amp; IMS Workbench
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            Ingest immutable GSTR-2B portal JSON or Excel (.json, .xlsx) artifacts (max 25MB) and manage statutory Invoice Management System (IMS) actions.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {registrations.length > 0 ? (
            <select
              value={registrationId}
              onChange={(e) => {
                setRegistrationId(e.target.value);
                saveSession({ registrationId: e.target.value, snapshotId: "" });
              }}
              className="px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-mono bg-white"
            >
              {registrations.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.gstin} ({r.state_code})
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              placeholder="Registration UUID"
              value={registrationId}
              onChange={(e) => {
                setRegistrationId(e.target.value);
                saveSession({ registrationId: e.target.value });
              }}
              className="px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-mono"
            />
          )}

          <input
            type="month"
            value={returnPeriod}
            onChange={(e) => {
              setReturnPeriod(e.target.value);
              saveSession({ returnPeriod: e.target.value });
            }}
            className="px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-mono"
          />

          <button
            onClick={() => loadSnapshotsAndDocs(registrationId, returnPeriod, activeSnapshotId)}
            disabled={loading || !registrationId}
            className="px-3 py-1.5 bg-slate-100 hover:bg-slate-200 disabled:bg-slate-50 text-slate-700 rounded-lg text-xs font-semibold flex items-center gap-1.5"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>

      {errorMsg && (
        <div className="bg-rose-50 border border-rose-200 rounded-xl p-3 text-xs text-rose-800 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}

      {uploadResult && (
        <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-3 text-xs text-emerald-800 flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{uploadResult}</span>
        </div>
      )}

      {/* Dropzone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragging(false);
          const file = e.dataTransfer.files[0];
          if (file) handleFileUpload(file);
        }}
        className={`border-2 border-dashed rounded-xl p-8 text-center transition-colors ${
          isDragging
            ? "border-indigo-500 bg-indigo-50/60"
            : "border-slate-300 bg-white hover:border-slate-400"
        }`}
      >
        <UploadCloud className="w-10 h-10 text-indigo-500 mx-auto mb-3" />
        <p className="text-sm font-medium text-slate-800">
          Drop your GSTN Portal GSTR-2B JSON or Excel file here
        </p>
        <p className="text-xs text-slate-400 mt-1">
          GSTIN:{" "}
          <span className="font-mono font-semibold text-slate-600">
            {selectedReg?.gstin || (registrationId ? "Selected Registration" : "None Selected")}
          </span>{" "}
          • Period: <span className="font-mono">{returnPeriod}</span> • Max 25MB (.json, .xlsx, .xls)
        </p>

        <label className="mt-4 inline-flex items-center px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-semibold cursor-pointer shadow-sm">
          <span>Select GSTR-2B Artifact</span>
          <input
            type="file"
            accept=".json,.xlsx,.xls"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleFileUpload(f);
            }}
          />
        </label>
      </div>

      {/* Snapshot Summary Bar */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-5 flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-wrap items-center gap-6 text-xs">
          <div>
            <span className="text-slate-400 block">Active Snapshot Version</span>
            <span className="font-mono font-bold text-slate-900">
              {currentSnapshot ? `v${currentSnapshot.version_number} (${currentSnapshot.id.slice(0, 8)}...)` : "None Loaded"}
            </span>
          </div>
          <div>
            <span className="text-slate-400 block">Total Portal Docs</span>
            <span className="font-mono font-bold text-slate-900">
              {currentSnapshot ? currentSnapshot.total_documents : docs.length}
            </span>
          </div>
          <div>
            <span className="text-slate-400 block">Portal Eligible ITC</span>
            <span className="font-mono font-bold text-emerald-700">
              ₹{Number(currentSnapshot?.total_itc_available || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
            </span>
          </div>
          <div>
            <span className="text-slate-400 block">Lock State</span>
            {currentSnapshot?.is_frozen_by_gstr3b ? (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-rose-100 text-rose-800 font-semibold">
                <Lock className="w-3 h-3" /> Frozen by GSTR-3B
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 font-semibold">
                Open for IMS Action
              </span>
            )}
          </div>
        </div>

        <button
          onClick={handleRecompute}
          disabled={!activeSnapshotId || loading || Boolean(currentSnapshot?.is_frozen_by_gstr3b)}
          className="px-4 py-2 bg-slate-900 hover:bg-slate-800 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold transition"
        >
          Recompute Snapshot from IMS
        </button>
      </div>

      {/* Portal Documents & IMS Table */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200 bg-slate-50/70 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-bold text-slate-800">GSTR-2B Document Register</h3>
            <p className="text-xs text-slate-500">
              Review supplier-uploaded documents and apply ACCEPTED / REJECTED / PENDING IMS decisions.
            </p>
          </div>
          <span className="text-xs font-mono text-slate-500">{docs.length} rows</span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
                <th className="px-4 py-3">Document &amp; Date</th>
                <th className="px-4 py-3">Supplier</th>
                <th className="px-4 py-3 text-right">Taxable (₹)</th>
                <th className="px-4 py-3 text-right">Tax (₹)</th>
                <th className="px-4 py-3 text-center">Portal Status</th>
                <th className="px-4 py-3 text-center">IMS Decision</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 font-mono">
              {docs.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center font-sans text-slate-500">
                    No GSTR-2B documents found for this registration and period. Upload a JSON or Excel (.json, .xlsx) file above to populate live records.
                  </td>
                </tr>
              ) : (
                docs.map((doc) => (
                  <tr key={doc.id} className="hover:bg-slate-50/80">
                    <td className="px-4 py-3">
                      <div className="font-bold text-slate-900">{doc.document_number}</div>
                      <div className="text-[11px] text-slate-500 font-sans">
                        {doc.document_date} • {doc.document_type || "B2B"}
                      </div>
                    </td>
                    <td className="px-4 py-3 font-sans">
                      <div className="font-medium text-slate-800">{doc.supplier_name || "Supplier"}</div>
                      <div className="text-[11px] font-mono text-slate-500">{doc.supplier_gstin}</div>
                    </td>
                    <td className="px-4 py-3 text-right">
                      {Number(doc.taxable_value || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                    </td>
                    <td className="px-4 py-3 text-right font-bold">
                      {sumGstr2bDocTax(doc).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                    </td>
                    <td className="px-4 py-3 text-center font-sans">
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                          ["y", "available"].includes(String(doc.itc_availability || "").toLowerCase())
                            ? "bg-emerald-100 text-emerald-800"
                            : "bg-rose-100 text-rose-800"
                        }`}
                      >
                        {doc.itc_availability}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-center font-sans">
                      <div className="inline-flex items-center gap-1">
                        <button
                          onClick={() => handleImsUpdate(doc.id, "ACCEPTED")}
                          disabled={Boolean(currentSnapshot?.is_frozen_by_gstr3b)}
                          className={`px-2 py-1 rounded text-[10px] font-semibold inline-flex items-center gap-1 ${
                            doc.ims_action === "ACCEPTED"
                              ? "bg-emerald-600 text-white"
                              : "bg-slate-100 text-slate-600 hover:bg-emerald-50 hover:text-emerald-700"
                          }`}
                        >
                          <CheckCircle2 className="w-3 h-3" /> Accept
                        </button>
                        <button
                          onClick={() => handleImsUpdate(doc.id, "PENDING")}
                          disabled={Boolean(currentSnapshot?.is_frozen_by_gstr3b)}
                          className={`px-2 py-1 rounded text-[10px] font-semibold inline-flex items-center gap-1 ${
                            doc.ims_action === "PENDING"
                              ? "bg-amber-500 text-white"
                              : "bg-slate-100 text-slate-600 hover:bg-amber-50 hover:text-amber-700"
                          }`}
                        >
                          <Clock className="w-3 h-3" /> Pending
                        </button>
                        <button
                          onClick={() => handleImsUpdate(doc.id, "REJECTED")}
                          disabled={Boolean(currentSnapshot?.is_frozen_by_gstr3b)}
                          className={`px-2 py-1 rounded text-[10px] font-semibold inline-flex items-center gap-1 ${
                            doc.ims_action === "REJECTED"
                              ? "bg-rose-600 text-white"
                              : "bg-slate-100 text-slate-600 hover:bg-rose-50 hover:text-rose-700"
                          }`}
                        >
                          <XCircle className="w-3 h-3" /> Reject
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-xs text-amber-900 flex items-start gap-3">
        <ShieldAlert className="w-4 h-4 shrink-0 mt-0.5 text-amber-700" />
        <span>
          Under GST IMS rules, documents marked <strong>PENDING</strong> or <strong>REJECTED</strong> are excluded from provisional GSTR-2B eligible ITC on snapshot recompute. Once Form GSTR-3B is filed, IMS actions freeze immutably for audit provenance.
        </span>
      </div>
    </div>
  );
};

export default Gstr2bUpload;
