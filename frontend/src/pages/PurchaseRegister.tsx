import React, { useState } from "react";
import { BookOpen, PlusCircle, CheckCircle, Clock, ShieldCheck, DollarSign, Filter, ChevronRight } from "lucide-react";

interface PurchaseDoc {
  id: string;
  document_number: string;
  document_date: string;
  supplier_gstin: string;
  supplier_legal_name: string;
  place_of_supply: string;
  total_taxable_amount: string;
  tax_amount: string;
  total_invoice_value: string;
  status: "draft" | "pending_approval" | "approved";
  is_reverse_charge: boolean;
  goods_services_type: string;
  receipt_status: "UNKNOWN" | "NOT_RECEIVED" | "PARTIALLY_RECEIVED" | "RECEIVED" | "DEEMED_RECEIVED";
  delivery_proof_ref?: string;
  lot_info?: string;
}

export const PurchaseRegister: React.FC = () => {
  const [documents, setDocuments] = useState<PurchaseDoc[]>([
    {
      id: "doc-pur-001",
      document_number: "INV-2026-101",
      document_date: "2026-11-04",
      supplier_gstin: "27AAGCB1234F1ZQ",
      supplier_legal_name: "Apex Engineering Supplies Pvt Ltd",
      place_of_supply: "27",
      total_taxable_amount: "1,00,000.00",
      tax_amount: "18,000.00",
      total_invoice_value: "1,18,000.00",
      status: "approved",
      is_reverse_charge: false,
      goods_services_type: "goods",
      receipt_status: "RECEIVED",
      delivery_proof_ref: "GRN-2026-8812"
    },
    {
      id: "doc-pur-002",
      document_number: "BLR/9921",
      document_date: "2026-11-08",
      supplier_gstin: "29AABCB5678K1ZM",
      supplier_legal_name: "CloudTech Software Solutions LLP",
      place_of_supply: "29",
      total_taxable_amount: "50,000.00",
      tax_amount: "9,000.00",
      total_invoice_value: "59,000.00",
      status: "draft",
      is_reverse_charge: false,
      goods_services_type: "services",
      receipt_status: "UNKNOWN"
    },
    {
      id: "doc-pur-003",
      document_number: "LOG-NOV-40",
      document_date: "2026-11-12",
      supplier_gstin: "27AAAPL9012L1ZV",
      supplier_legal_name: "Shree Ganesh Road Carriers",
      place_of_supply: "27",
      total_taxable_amount: "20,000.00",
      tax_amount: "1,000.00",
      total_invoice_value: "21,000.00",
      status: "pending_approval",
      is_reverse_charge: true,
      goods_services_type: "services",
      receipt_status: "RECEIVED",
      delivery_proof_ref: "LR-CN-88102"
    },
    {
      id: "doc-pur-004",
      document_number: "LOT-2026-99",
      document_date: "2026-11-14",
      supplier_gstin: "27AABCA9911F1ZZ",
      supplier_legal_name: "Bharat Raw Materials Corp",
      place_of_supply: "27",
      total_taxable_amount: "80,000.00",
      tax_amount: "14,400.00",
      total_invoice_value: "94,400.00",
      status: "approved",
      is_reverse_charge: false,
      goods_services_type: "goods",
      receipt_status: "PARTIALLY_RECEIVED",
      lot_info: "Lot 1 of 2 (Proviso Hold)",
      delivery_proof_ref: "DC-LOT-01"
    }
  ]);

  const [showAddForm, setShowAddForm] = useState(false);
  const [showPaymentModal, setShowPaymentModal] = useState<string | null>(null);
  const [showReceiptModal, setShowReceiptModal] = useState<string | null>(null);
  const [receiptProofRef, setReceiptProofRef] = useState("GRN-2026-");
  const [receiptDate, setReceiptDate] = useState("2026-11-18");
  const [selectedReceiptStatus, setSelectedReceiptStatus] = useState<"RECEIVED" | "PARTIALLY_RECEIVED">("RECEIVED");

  // Form State
  const [docNum, setDocNum] = useState("");
  const [docDate, setDocDate] = useState("2026-11-15");
  const [supplierGstin, setSupplierGstin] = useState("");
  const [supplierName, setSupplierName] = useState("");
  const [taxableVal, setTaxableVal] = useState("");
  const [taxVal, setTaxVal] = useState("");
  const [pos, setPos] = useState("27");
  const [isRcm, setIsRcm] = useState(false);

  // Payment Modal State
  const [payRef, setPayRef] = useState("NEFT-UTR-89123");
  const [payDate, setPayDate] = useState("2026-11-17");
  const [payMode, setPayMode] = useState("NEFT");
  const [payAmount, setPayAmount] = useState("");

  const handleCreateDocument = (e: React.FormEvent) => {
    e.preventDefault();
    const taxable = parseFloat(taxableVal) || 0;
    const tax = parseFloat(taxVal) || 0;
    const total = taxable + tax;

    const newDoc: PurchaseDoc = {
      id: `doc-pur-${Date.now()}`,
      document_number: docNum,
      document_date: docDate,
      supplier_gstin: supplierGstin,
      supplier_legal_name: supplierName,
      place_of_supply: pos,
      total_taxable_amount: taxable.toLocaleString("en-IN", { minimumFractionDigits: 2 }),
      tax_amount: tax.toLocaleString("en-IN", { minimumFractionDigits: 2 }),
      total_invoice_value: total.toLocaleString("en-IN", { minimumFractionDigits: 2 }),
      status: "draft",
      is_reverse_charge: isRcm,
      goods_services_type: "goods",
      receipt_status: "UNKNOWN"
    };

    setDocuments(prev => [newDoc, ...prev]);
    setShowAddForm(false);
    setDocNum("");
    setSupplierGstin("");
    setSupplierName("");
    setTaxableVal("");
    setTaxVal("");
  };

  const handleApprove = (docId: string) => {
    setDocuments(prev => prev.map(doc => doc.id === docId ? { ...doc, status: "approved" } : doc));
  };

  const handleRecordPayment = (e: React.FormEvent) => {
    e.preventDefault();
    setShowPaymentModal(null);
    setPayAmount("");
  };

  const handleVerifyReceipt = (e: React.FormEvent) => {
    e.preventDefault();
    if (!showReceiptModal) return;
    setDocuments(prev => prev.map(doc => 
      doc.id === showReceiptModal 
        ? { 
            ...doc, 
            receipt_status: selectedReceiptStatus, 
            delivery_proof_ref: receiptProofRef,
            lot_info: selectedReceiptStatus === "PARTIALLY_RECEIVED" ? "Lot 1 of 2 (Proviso Hold)" : undefined
          } 
        : doc
    ));
    setShowReceiptModal(null);
  };

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-indigo-600" />
            Independent Purchase Register
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            Buyer books of accounts maintained separately from GSTR-2B. Enforces strict human approval before reconciliation and payment tracking.
          </p>
        </div>
        <button
          onClick={() => setShowAddForm(!showAddForm)}
          className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-medium tracking-wide shadow-sm flex items-center gap-2 transition-colors self-start md:self-auto"
        >
          <PlusCircle className="w-4 h-4" />
          {showAddForm ? "Cancel Entry" : "Record Inward Invoice"}
        </button>
      </div>

      {/* Add Document Drawer / Form */}
      {showAddForm && (
        <form onSubmit={handleCreateDocument} className="bg-slate-50 border border-slate-300 rounded-xl p-6 space-y-4 shadow-inner">
          <h3 className="text-sm font-bold text-slate-800 border-b border-slate-200 pb-2 flex items-center justify-between">
            <span>New Inward Purchase Document</span>
            <span className="text-[11px] font-normal text-indigo-700 bg-indigo-50 px-2 py-0.5 rounded border border-indigo-200">
              Registration Scoped under PAN: AABCB1234M
            </span>
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 text-xs">
            <div>
              <label className="block font-semibold text-slate-700 mb-1">GST Registration (Branch)</label>
              <select
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
              >
                <option value="reg-01">27AABCB1234M1ZT (MH Primary)</option>
                <option value="reg-02">24AABCB1234M1ZR (GJ Branch)</option>
              </select>
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Document Type</label>
              <select
                className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
              >
                <option value="tax_invoice">Tax Invoice</option>
                <option value="debit_note">Debit Note</option>
                <option value="credit_note">Credit Note</option>
                <option value="rcm_self_invoice">RCM Self Invoice</option>
              </select>
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Supplier GSTIN</label>
              <input
                type="text"
                value={supplierGstin}
                onChange={e => setSupplierGstin(e.target.value.toUpperCase())}
                placeholder="27ABCDE1234F1Z5"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Supplier Legal Name</label>
              <input
                type="text"
                value={supplierName}
                onChange={e => setSupplierName(e.target.value)}
                placeholder="Vendor Trade Name"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Invoice / Doc Number</label>
              <input
                type="text"
                value={docNum}
                onChange={e => setDocNum(e.target.value)}
                placeholder="INV/2026/001"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Invoice Date</label>
              <input
                type="date"
                value={docDate}
                onChange={e => setDocDate(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Place of Supply (POS)</label>
              <input
                type="text"
                value={pos}
                onChange={e => setPos(e.target.value)}
                placeholder="27"
                maxLength={2}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Reverse Charge (RCM)?</label>
              <select
                value={isRcm ? "yes" : "no"}
                onChange={e => setIsRcm(e.target.value === "yes")}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
              >
                <option value="no">No (Regular Supply)</option>
                <option value="yes">Yes (Inward RCM)</option>
              </select>
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Taxable Value (₹)</label>
              <input
                type="number"
                step="0.01"
                value={taxableVal}
                onChange={e => setTaxableVal(e.target.value)}
                placeholder="0.00"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Total Tax Amount (₹)</label>
              <input
                type="number"
                step="0.01"
                value={taxVal}
                onChange={e => setTaxVal(e.target.value)}
                placeholder="0.00"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="px-4 py-2 border border-slate-300 text-slate-700 rounded-lg text-xs hover:bg-slate-100"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-medium"
            >
              Save as Draft Inward Invoice
            </button>
          </div>
        </form>
      )}

      {/* Documents Table */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
          <h3 className="text-sm font-bold text-slate-800">
            Recorded Inward Purchase Documents ({documents.length})
          </h3>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">Filter by Status:</span>
            <select className="text-xs border border-slate-200 rounded px-2 py-1 bg-slate-50">
              <option value="all">All Documents</option>
              <option value="draft">Draft</option>
              <option value="approved">Approved</option>
            </select>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
              <tr>
                <th className="px-4 py-3">Doc Number & Date</th>
                <th className="px-4 py-3">Supplier Details</th>
                <th className="px-4 py-3 text-right">Taxable (₹)</th>
                <th className="px-4 py-3 text-right">Tax (₹)</th>
                <th className="px-4 py-3 text-right">Total Value (₹)</th>
                <th className="px-4 py-3 text-center">Sec 16(2)(b) Receipt Gate</th>
                <th className="px-4 py-3 text-center">Approval Status</th>
                <th className="px-4 py-3 text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 font-mono">
              {documents.map((doc) => (
                <tr key={doc.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3">
                    <div className="font-bold text-slate-900">{doc.document_number}</div>
                    <div className="text-[11px] text-slate-500 font-sans">{doc.document_date}</div>
                  </td>
                  <td className="px-4 py-3 font-sans">
                    <div className="font-medium text-slate-800">{doc.supplier_legal_name}</div>
                    <div className="text-[11px] text-slate-500 font-mono">{doc.supplier_gstin} (POS: {doc.place_of_supply})</div>
                  </td>
                  <td className="px-4 py-3 text-right font-medium text-slate-800">{doc.total_taxable_amount}</td>
                  <td className="px-4 py-3 text-right font-medium text-slate-800">{doc.tax_amount}</td>
                  <td className="px-4 py-3 text-right font-bold text-slate-900">{doc.total_invoice_value}</td>
                  <td className="px-4 py-3 text-center font-sans">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                      doc.receipt_status === 'RECEIVED'
                        ? 'bg-emerald-100 text-emerald-800'
                        : doc.receipt_status === 'PARTIALLY_RECEIVED'
                        ? 'bg-purple-100 text-purple-800'
                        : doc.receipt_status === 'DEEMED_RECEIVED'
                        ? 'bg-sky-100 text-sky-800'
                        : 'bg-amber-100 text-amber-800'
                    }`}>
                      {doc.receipt_status}
                    </span>
                    {doc.delivery_proof_ref && (
                      <div className="text-[9px] text-slate-500 font-mono mt-0.5">{doc.delivery_proof_ref}</div>
                    )}
                    {doc.lot_info && (
                      <div className="text-[9px] text-purple-700 font-sans mt-0.5">{doc.lot_info}</div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-sans font-semibold ${
                      doc.status === 'approved' 
                        ? 'bg-emerald-100 text-emerald-800' 
                        : doc.status === 'pending_approval'
                        ? 'bg-amber-100 text-amber-800'
                        : 'bg-slate-200 text-slate-700'
                    }`}>
                      {doc.status.replace("_", " ").toUpperCase()}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center font-sans space-x-1">
                    {doc.status !== "approved" && (
                      <button
                        onClick={() => handleApprove(doc.id)}
                        className="px-2 py-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-[10px] font-medium"
                      >
                        Approve
                      </button>
                    )}
                    <button
                      onClick={() => {
                        setShowReceiptModal(doc.id);
                        setReceiptProofRef(doc.delivery_proof_ref || "GRN-2026-");
                      }}
                      className="px-2 py-1 bg-sky-50 hover:bg-sky-100 text-sky-700 rounded text-[10px] font-medium border border-sky-200"
                    >
                      Receipt Gate
                    </button>
                    <button
                      onClick={() => setShowPaymentModal(doc.id)}
                      className="px-2 py-1 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded text-[10px] font-medium border border-indigo-200"
                    >
                      Pay (Rule 37)
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Payment Drawer Modal */}
      {showPaymentModal && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-xl shadow-xl border border-slate-200 max-w-md w-full p-6 space-y-4">
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <DollarSign className="w-4 h-4 text-emerald-600" />
              Record Vendor Payment (Rule 37 180-Day Safe Harbour)
            </h3>
            <p className="text-xs text-slate-500">
              Allocates payment against this inward document to protect ITC from Rule 37 reversal and 18% statutory interest.
            </p>

            <form onSubmit={handleRecordPayment} className="space-y-3 text-xs">
              <div>
                <label className="block font-semibold text-slate-700 mb-1">Payment Reference / UTR</label>
                <input
                  type="text"
                  value={payRef}
                  onChange={e => setPayRef(e.target.value)}
                  className="w-full px-3 py-2 border border-slate-300 rounded font-mono"
                  required
                />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Payment Date</label>
                  <input
                    type="date"
                    value={payDate}
                    onChange={e => setPayDate(e.target.value)}
                    className="w-full px-3 py-2 border border-slate-300 rounded"
                    required
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Mode</label>
                  <select
                    value={payMode}
                    onChange={e => setPayMode(e.target.value)}
                    className="w-full px-3 py-2 border border-slate-300 rounded"
                  >
                    <option value="NEFT">NEFT / RTGS</option>
                    <option value="UPI">UPI</option>
                    <option value="CHEQUE">Cheque</option>
                    <option value="CASH">Cash</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="block font-semibold text-slate-700 mb-1">Allocated Amount Paid (₹)</label>
                <input
                  type="number"
                  step="0.01"
                  value={payAmount}
                  onChange={e => setPayAmount(e.target.value)}
                  placeholder="Full or partial amount"
                  className="w-full px-3 py-2 border border-slate-300 rounded font-mono"
                  required
                />
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowPaymentModal(null)}
                  className="px-3 py-1.5 border border-slate-300 rounded text-slate-700 text-xs"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-xs font-medium"
                >
                  Save & Allocate Payment
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Section 16(2)(b) Receipt Verification Modal */}
      {showReceiptModal && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-xl shadow-xl border border-slate-200 max-w-md w-full p-6 space-y-4">
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-sky-600" />
              Section 16(2)(b) Receipt Gate Verification
            </h3>
            <p className="text-xs text-slate-500">
              Statutory verification required before availing ITC. Under Section 16(2)(b) first proviso, supplies in lots require final lot receipt before full ITC claim.
            </p>

            <form onSubmit={handleVerifyReceipt} className="space-y-3 text-xs">
              <div>
                <label className="block font-semibold text-slate-700 mb-1">Receipt Status</label>
                <select
                  value={selectedReceiptStatus}
                  onChange={e => setSelectedReceiptStatus(e.target.value as any)}
                  className="w-full px-3 py-2 border border-slate-300 rounded"
                >
                  <option value="RECEIVED">RECEIVED (Full Delivery / Service Completion)</option>
                  <option value="PARTIALLY_RECEIVED">PARTIALLY_RECEIVED (Intermediate Lot - Proviso Hold)</option>
                </select>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Receipt Date</label>
                  <input
                    type="date"
                    value={receiptDate}
                    onChange={e => setReceiptDate(e.target.value)}
                    className="w-full px-3 py-2 border border-slate-300 rounded"
                    required
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Proof Reference (GRN / Challan / Cert)</label>
                  <input
                    type="text"
                    value={receiptProofRef}
                    onChange={e => setReceiptProofRef(e.target.value)}
                    placeholder="GRN-2026-XXXX"
                    className="w-full px-3 py-2 border border-slate-300 rounded font-mono"
                    required
                  />
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowReceiptModal(null)}
                  className="px-3 py-1.5 border border-slate-300 rounded text-slate-700 text-xs"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-3 py-1.5 bg-sky-600 hover:bg-sky-700 text-white rounded text-xs font-medium"
                >
                  Save Receipt Verification
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
