import React, { useState } from "react";
import { UploadCloud, CheckCircle2, AlertTriangle, FileSpreadsheet, ShieldCheck, Layers, FileCode } from "lucide-react";
import { API_BASE_URL, authHeaders, getStoredSession, saveSession } from "../api";

interface Gstr2bUploadProps {
  onUploadSuccess?: (data: Gstr2bUploadResponse) => void;
}

interface Gstr2bUploadResponse {
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
  recomputed_status?: string;
}

interface Gstr2bRecomputeResponse {
  new_snapshot_id: string;
  previous_snapshot_id: string;
  version_number: number;
  return_period: string;
  recipient_gstin: string;
  total_itc_available: string;
  total_itc_not_available: string;
  accepted_docs_count: number;
  pending_docs_held_count: number;
  rejected_docs_count: number;
  recomputed_at: string;
}

const storedSession = getStoredSession();

export const Gstr2bUpload: React.FC<Gstr2bUploadProps> = ({ onUploadSuccess }) => {
  const [returnPeriod, setReturnPeriod] = useState("2026-11");
  const [registrationGstin, setRegistrationGstin] = useState("27AABCB1234M1ZT");
  const [registrationId, setRegistrationId] = useState<string>(storedSession.registrationId);
  const [userId, setUserId] = useState<string>(storedSession.userId);
  const [orgId, setOrgId] = useState<string>(storedSession.orgId);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileHash, setFileHash] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isRecomputing, setIsRecomputing] = useState(false);
  const [uploadResult, setUploadResult] = useState<Gstr2bUploadResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const buildAuthHeaders = (): Record<string, string> => {
    if (!userId.trim() || !orgId.trim()) {
      throw new Error("Enter User ID and Organization ID for the development identity headers.");
    }
    saveSession({ userId: userId.trim(), orgId: orgId.trim(), registrationId: registrationId.trim(), returnPeriod: returnPeriod.trim() });
    return authHeaders();
  };

  const parseError = async (response: Response): Promise<string> => {
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") return body.detail;
      if (Array.isArray(body?.detail)) return body.detail.map((d: any) => d?.msg ?? JSON.stringify(d)).join("; ");
      return `Request failed with status ${response.status}.`;
    } catch {
      return `Request failed with status ${response.status}.`;
    }
  };

  const computeSha256 = async (file: File): Promise<string> => {
    const buffer = await file.arrayBuffer();
    const hashBuffer = await crypto.subtle.digest("SHA-256", buffer);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map(b => b.toString(16).padStart(2, "0")).join("");
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    setErrorMsg(null);
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      setSelectedFile(file);
      try {
        const hash = await computeSha256(file);
        setFileHash(hash);
      } catch {
        setFileHash(null);
        setErrorMsg("Could not compute the file hash in this browser context. You can still try the upload.");
      }
    }
  };

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile) {
      setErrorMsg("Please select a GSTR-2B JSON or Excel file.");
      return;
    }
    if (!registrationId.trim()) {
      setErrorMsg("Enter the GST registration ID.");
      return;
    }

    setIsUploading(true);
    setErrorMsg(null);

    try {
      const formData = new FormData();
      formData.append("registration_id", registrationId.trim());
      formData.append("return_period", returnPeriod.trim());
      formData.append("file", selectedFile);

      const response = await fetch(`${API_BASE_URL}/api/v1/reconciliation/gstr2b/upload`, {
        method: "POST",
        headers: buildAuthHeaders(),
        body: formData,
      });

      if (!response.ok) {
        throw new Error(await parseError(response));
      }

      const data = (await response.json()) as Gstr2bUploadResponse;
      saveSession({ snapshotId: data.snapshot_id, returnPeriod: data.return_period });
      setUploadResult(data);
      if (onUploadSuccess) onUploadSuccess(data);
    } catch (err: any) {
      setErrorMsg(err?.message || "Upload failed. Please check the backend URL, file format, and development identity headers.");
    } finally {
      setIsUploading(false);
    }
  };

  const handleRecompute = async () => {
    if (!uploadResult) return;
    setIsRecomputing(true);
    setErrorMsg(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/reconciliation/gstr2b/recompute`, {
        method: "POST",
        headers: {
          ...buildAuthHeaders(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          registration_id: registrationId.trim(),
          return_period: uploadResult.return_period,
          snapshot_id: uploadResult.snapshot_id,
        }),
      });

      if (!response.ok) {
        throw new Error(await parseError(response));
      }

      const data = (await response.json()) as Gstr2bRecomputeResponse;
      saveSession({ snapshotId: data.new_snapshot_id, returnPeriod: data.return_period });
      setUploadResult((prev) =>
        prev
          ? {
              ...prev,
              snapshot_id: data.new_snapshot_id,
              version_number: data.version_number,
              total_itc_available: data.total_itc_available,
              total_itc_not_available: data.total_itc_not_available,
              recomputed_status: `Recomputed at ${data.recomputed_at} — accepted ${data.accepted_docs_count}, pending ${data.pending_docs_held_count}, rejected ${data.rejected_docs_count}.`,
            }
          : prev
      );
    } catch (err: any) {
      setErrorMsg(err?.message || "Recompute failed. Please check the backend response.");
    } finally {
      setIsRecomputing(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
        <div className="flex items-center justify-between pb-4 border-b border-slate-100">
          <div>
            <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
              <UploadCloud className="w-5 h-5 text-indigo-600" />
              GSTR-2B Statement Ingestion Engine
            </h2>
            <p className="text-xs text-slate-500 mt-1">
              Statutory read-only statement ingestion supporting multi-GSTIN registrations under PAN, versioned snapshots, and SHA-256 deduplication.
            </p>
          </div>
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-200">
            Engine v2.1
          </span>
        </div>

        <form onSubmit={handleUpload} className="mt-6 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
                Recipient GSTIN (PAN Registration)
              </label>
              <input
                type="text"
                value={registrationGstin}
                onChange={(e) => setRegistrationGstin(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
                placeholder="27AABCB1234M1ZT"
                required
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
                GSTR-2B Return Period
              </label>
              <input
                type="text"
                value={returnPeriod}
                onChange={(e) => setReturnPeriod(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
                placeholder="YYYY-MM (e.g. 2026-11)"
                required
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
                Registration ID
              </label>
              <input
                type="text"
                value={registrationId}
                onChange={(e) => setRegistrationId(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
                placeholder="GST registration UUID"
                required
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
                User ID (dev header)
              </label>
              <input
                type="text"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
                placeholder="User UUID"
                required
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
                Organization ID (dev header)
              </label>
              <input
                type="text"
                value={orgId}
                onChange={(e) => setOrgId(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
                placeholder="Organization UUID"
                required
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
              Select Statement File (GST Portal JSON or Excel)
            </label>
            <div className="mt-1 flex justify-center px-6 pt-5 pb-6 border-2 border-slate-300 border-dashed rounded-xl hover:border-indigo-400 transition-colors">
              <div className="space-y-1 text-center">
                <FileSpreadsheet className="mx-auto h-10 w-10 text-slate-400" />
                <div className="flex text-sm text-slate-600 justify-center">
                  <label className="relative cursor-pointer bg-white rounded-md font-medium text-indigo-600 hover:text-indigo-500 focus-within:outline-none">
                    <span>Choose GSTR-2B file</span>
                    <input
                      type="file"
                      accept=".json, .xlsx, .xls"
                      onChange={handleFileChange}
                      className="sr-only"
                    />
                  </label>
                  <p className="pl-1">or drag and drop</p>
                </div>
                <p className="text-xs text-slate-500">Official GST Portal JSON or Excel up to 50MB</p>
              </div>
            </div>
          </div>

          {selectedFile && (
            <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg space-y-1 text-xs">
              <div className="flex items-center justify-between font-medium text-slate-700">
                <span className="flex items-center gap-1.5">
                  <FileCode className="w-4 h-4 text-slate-500" />
                  {selectedFile.name} ({(selectedFile.size / 1024).toFixed(1)} KB)
                </span>
                <span className="text-emerald-600 font-medium flex items-center gap-1">
                  <CheckCircle2 className="w-3.5 h-3.5" /> Ready for Ingestion
                </span>
              </div>
              {fileHash && (
                <div className="text-slate-500 font-mono text-[11px] truncate">
                  SHA-256: {fileHash}
                </div>
              )}
            </div>
          )}

          {errorMsg && (
            <div className="p-3 bg-rose-50 border border-rose-200 text-rose-700 rounded-lg text-xs flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span>{errorMsg}</span>
            </div>
          )}

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={isUploading || !selectedFile}
              className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-medium tracking-wide shadow-sm transition-colors flex items-center gap-2"
            >
              {isUploading ? "Verifying & Ingesting..." : "Ingest & Create Immutable Snapshot"}
            </button>
          </div>
        </form>
      </div>

      {uploadResult && (
        <div className="bg-white rounded-xl shadow-sm border border-emerald-200 p-6 space-y-4">
          <div className="flex items-center justify-between pb-3 border-b border-emerald-100">
            <div className="flex items-center gap-2 text-emerald-800">
              <ShieldCheck className="w-5 h-5 text-emerald-600" />
              <h3 className="font-bold text-sm">GSTR-2B Statement Snapshot Created Successfully</h3>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleRecompute}
                disabled={isRecomputing}
                className="px-3 py-1 bg-indigo-50 hover:bg-indigo-100 disabled:bg-slate-100 text-indigo-700 border border-indigo-200 rounded text-xs font-semibold flex items-center gap-1.5 transition-colors"
              >
                <Layers className="w-3.5 h-3.5" />
                {isRecomputing ? "Recomputing..." : "Recompute Snapshot (After IMS Actions)"}
              </button>
              <span className="bg-emerald-100 text-emerald-800 px-2.5 py-1 rounded text-xs font-mono font-bold">
                Snapshot v{uploadResult.version_number} (Active)
              </span>
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 py-2">
            <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
              <p className="text-[11px] text-slate-500 font-medium">Recipient GSTIN</p>
              <p className="font-mono font-bold text-sm text-slate-800 mt-0.5">{uploadResult.recipient_gstin}</p>
            </div>
            <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
              <p className="text-[11px] text-slate-500 font-medium">Return Period</p>
              <p className="font-mono font-bold text-sm text-slate-800 mt-0.5">{uploadResult.return_period}</p>
            </div>
            <div className="bg-emerald-50 p-3 rounded-lg border border-emerald-100">
              <p className="text-[11px] text-emerald-700 font-medium">Total Part A ITC Available</p>
              <p className="font-mono font-bold text-sm text-emerald-800 mt-0.5">₹{uploadResult.total_itc_available}</p>
            </div>
            <div className="bg-rose-50 p-3 rounded-lg border border-rose-100">
              <p className="text-[11px] text-rose-700 font-medium">Total Part B ITC Ineligible</p>
              <p className="font-mono font-bold text-sm text-rose-800 mt-0.5">₹{uploadResult.total_itc_not_available}</p>
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs">
            <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
              <p className="text-slate-500 font-medium">Import ID</p>
              <p className="font-mono font-bold text-slate-800 mt-0.5 break-all">{uploadResult.import_id}</p>
            </div>
            <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
              <p className="text-slate-500 font-medium">Snapshot ID</p>
              <p className="font-mono font-bold text-slate-800 mt-0.5 break-all">{uploadResult.snapshot_id}</p>
            </div>
            <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
              <p className="text-slate-500 font-medium">Documents</p>
              <p className="font-mono font-bold text-slate-800 mt-0.5">{uploadResult.total_documents}</p>
            </div>
            <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
              <p className="text-slate-500 font-medium">Financial Year</p>
              <p className="font-mono font-bold text-slate-800 mt-0.5">{uploadResult.financial_year}</p>
            </div>
          </div>

          {uploadResult.recomputed_status && (
            <p className="text-xs text-slate-600 bg-indigo-50 border border-indigo-100 rounded-lg p-3">
              {uploadResult.recomputed_status}
            </p>
          )}
        </div>
      )}
    </div>
  );
};
