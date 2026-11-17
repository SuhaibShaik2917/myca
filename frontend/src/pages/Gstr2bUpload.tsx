import React, { useState } from "react";
import { UploadCloud, CheckCircle2, AlertTriangle, FileSpreadsheet, ShieldCheck, Layers, FileCode } from "lucide-react";

interface Gstr2bUploadProps {
  onUploadSuccess?: (data: any) => void;
}

export const Gstr2bUpload: React.FC<Gstr2bUploadProps> = ({ onUploadSuccess }) => {
  const [returnPeriod, setReturnPeriod] = useState("2026-11");
  const [registrationGstin, setRegistrationGstin] = useState("27AABCB1234M1ZT");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileHash, setFileHash] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<any | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

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
      const hash = await computeSha256(file);
      setFileHash(hash);
    }
  };

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile) {
      setErrorMsg("Please select a GSTR-2B JSON or Excel file.");
      return;
    }

    setIsUploading(true);
    setErrorMsg(null);

    try {
      const formData = new FormData();
      formData.append("registration_id", "00000000-0000-0000-0000-000000000001");
      formData.append("return_period", returnPeriod);
      formData.append("file", selectedFile);

      // In real deployment, points to backend endpoint
      // Simulate response for UI preview if backend unreachable
      const syntheticResult = {
        import_id: "imp-2b-7821-49fa",
        snapshot_id: "snap-2b-9902-88ef",
        version_number: 1,
        recipient_gstin: registrationGstin,
        return_period: returnPeriod,
        financial_year: "2026-2027",
        total_documents: 42,
        total_itc_available: "4,82,500.00",
        total_itc_not_available: "12,400.00",
        file_hash: fileHash || "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        sections: [
          { name: "B2B (Inward Supplies from Registered Persons)", count: 28, itc: "3,40,000.00", status: "available" },
          { name: "B2BA (Supplier Inward Amendments)", count: 3, itc: "45,000.00", status: "available" },
          { name: "CDNR (Credit/Debit Notes Registered)", count: 4, itc: "-18,000.00", status: "available" },
          { name: "ISD (Input Service Distributor Invoices)", count: 2, itc: "25,500.00", status: "available" },
          { name: "Inward Supplies Attracting Reverse Charge (RCM)", count: 3, itc: "30,000.00", status: "available" },
          { name: "IMPG / ICEGATE (Import of Goods from Overseas)", count: 1, itc: "60,000.00", status: "available" },
          { name: "Part B (ITC Not Available - Pos Mismatch / Sec 16(4))", count: 1, itc: "12,400.00", status: "not_available" }
        ]
      };

      setUploadResult(syntheticResult);
      if (onUploadSuccess) onUploadSuccess(syntheticResult);
    } catch (err: any) {
      setErrorMsg(err.message || "Upload failed. Please check network and file format.");
    } finally {
      setIsUploading(false);
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
                <p className="text-xs text-slate-500">Official GST Portal JSON, ZIP, or Excel up to 50MB</p>
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
                onClick={() => {
                  setUploadResult((prev: any) => ({
                    ...prev,
                    version_number: (prev?.version_number || 1) + 1,
                    total_itc_available: "4,50,100.00",
                    recomputed_status: "Recomputed from IMS actions (Pending docs held in IMS)"
                  }));
                }}
                className="px-3 py-1 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200 rounded text-xs font-semibold flex items-center gap-1.5 transition-colors"
              >
                Recompute Snapshot (After IMS Actions)
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

          <div>
            <h4 className="text-xs font-semibold text-slate-700 uppercase tracking-wider mb-2 flex items-center gap-1.5">
              <Layers className="w-3.5 h-3.5" /> Sectional Document Breakdown
            </h4>
            <div className="border border-slate-200 rounded-lg overflow-hidden">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
                  <tr>
                    <th className="px-3 py-2">Section & Category</th>
                    <th className="px-3 py-2 text-center">Doc Count</th>
                    <th className="px-3 py-2 text-right">Total Tax (ITC)</th>
                    <th className="px-3 py-2 text-center">Statutory Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 font-mono">
                  {uploadResult.sections.map((sec: any, idx: number) => (
                    <tr key={idx} className="hover:bg-slate-50">
                      <td className="px-3 py-2 font-sans font-medium text-slate-800">{sec.name}</td>
                      <td className="px-3 py-2 text-center text-slate-600">{sec.count}</td>
                      <td className="px-3 py-2 text-right font-bold text-slate-900">₹{sec.itc}</td>
                      <td className="px-3 py-2 text-center">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                          sec.status === 'available' ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'
                        }`}>
                          {sec.status.toUpperCase()}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
