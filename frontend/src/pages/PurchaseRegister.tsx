import React, { useEffect, useState, useCallback } from "react";
import {
  PlusCircle,
  CheckCircle2,
  DollarSign,
  BookOpen,
  ShieldCheck,
  AlertTriangle,
  Truck,
  Layers,
  AlertCircle,
  RefreshCw,
} from "lucide-react";
import {
  loadSession,
  saveSession,
  listPurchaseDocuments,
  createPurchaseDocument,
  approvePurchaseDocument,
  verifyPurchaseReceipt,
  listPurchaseLots,
  recordPurchaseLot,
  recordPurchasePayment,
  PurchaseDocumentApi,
  LotLedgerEntryOut,
  GstRegistration,
} from "../api";

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
  raw_taxable: number;
  raw_tax: number;
  raw_total: number;
  status: string;
  is_reverse_charge: boolean;
  goods_services_type: "goods" | "services" | "both" | "capital_goods";
  receipt_status: "RECEIVED" | "PARTIALLY_RECEIVED" | "NOT_RECEIVED" | "DEEMED_RECEIVED" | "UNKNOWN";
  delivery_proof_ref?: string;
  lot_info?: string;
}

interface Props {
  registrations?: GstRegistration[];
  orgPan?: string;
}

function fmtMoney(n: number): string {
  return n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function mapDocument(row: PurchaseDocumentApi): PurchaseDoc {
  const taxable = Number(row.total_taxable_amount || 0);
  const tax =
    Number(row.cgst_amount || 0) +
    Number(row.sgst_amount || 0) +
    Number(row.igst_amount || 0) +
    Number(row.cess_amount || 0);
  const total = Number(row.total_invoice_value || taxable + tax);
  const receiptRaw = String(row.receipt_status || "UNKNOWN").toUpperCase();
  const validReceipts = new Set(["RECEIVED", "PARTIALLY_RECEIVED", "NOT_RECEIVED", "DEEMED_RECEIVED", "UNKNOWN"]);
  const receiptStatus = (validReceipts.has(receiptRaw) ? receiptRaw : "UNKNOWN") as PurchaseDoc["receipt_status"];
  const goodsTypeRaw = String(row.goods_services_type || "goods").toLowerCase();
  const validGoods = new Set(["goods", "services", "both", "capital_goods"]);
  const goodsType = (validGoods.has(goodsTypeRaw) ? goodsTypeRaw : "goods") as PurchaseDoc["goods_services_type"];

  let lotInfo: string | undefined;
  if (row.total_lots_expected && row.total_lots_expected > 1) {
    lotInfo = `Lot ${row.lots_received_count || 0} of ${row.total_lots_expected}${
      row.is_last_lot_received ? " (Final)" : " (Proviso Hold)"
    }`;
  }

  return {
    id: row.id,
    document_number: row.document_number,
    document_date: row.document_date,
    supplier_gstin: row.supplier_gstin || "UNREGISTERED",
    supplier_legal_name: row.supplier_legal_name || "Supplier",
    place_of_supply: row.place_of_supply || "",
    total_taxable_amount: fmtMoney(taxable),
    tax_amount: fmtMoney(tax),
    total_invoice_value: fmtMoney(total),
    raw_taxable: taxable,
    raw_tax: tax,
    raw_total: total,
    status: row.status,
    is_reverse_charge: Boolean(row.is_reverse_charge),
    goods_services_type: goodsType,
    receipt_status: receiptStatus,
    delivery_proof_ref:
      row.goods_receipt_note_ref ||
      row.delivery_challan_ref ||
      row.service_acceptance_cert_ref ||
      row.deemed_receipt_reason ||
      undefined,
    lot_info: lotInfo,
  };
}

export const PurchaseRegister: React.FC<Props> = ({ registrations = [], orgPan = "" }) => {
  const session = loadSession();
  const [selectedRegId, setSelectedRegId] = useState(
    session.registrationId || registrations[0]?.id || ""
  );
  const [returnPeriod, setReturnPeriod] = useState(session.returnPeriod || "2026-11");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [documents, setDocuments] = useState<PurchaseDoc[]>([]);

  const [showAddForm, setShowAddForm] = useState(false);
  const [showPaymentModal, setShowPaymentModal] = useState<PurchaseDoc | null>(null);
  const [showReceiptModal, setShowReceiptModal] = useState<PurchaseDoc | null>(null);
  const [showLotModal, setShowLotModal] = useState<PurchaseDoc | null>(null);
  const [lotsHistory, setLotsHistory] = useState<LotLedgerEntryOut[]>([]);

  // Receipt Modal State
  const [receiptProofRef, setReceiptProofRef] = useState("");
  const [receiptDate, setReceiptDate] = useState("2026-11-18");
  const [selectedReceiptStatus, setSelectedReceiptStatus] = useState<
    "RECEIVED" | "PARTIALLY_RECEIVED" | "DEEMED_RECEIVED"
  >("RECEIVED");

  // Lot Modal State
  const [lotNum, setLotNum] = useState(1);
  const [lotQty, setLotQty] = useState("100.00");
  const [lotRef, setLotRef] = useState("");
  const [lotGrn, setLotGrn] = useState("");
  const [lotCarrier, setLotCarrier] = useState("");
  const [lotVehicle, setLotVehicle] = useState("");
  const [lotEway, setLotEway] = useState("");

  // Add Document State
  const [docType, setDocType] = useState("tax_invoice");
  const [docNum, setDocNum] = useState("");
  const [docDate, setDocDate] = useState("2026-11-15");
  const [supplierGstin, setSupplierGstin] = useState("");
  const [supplierName, setSupplierName] = useState("");
  const [taxableVal, setTaxableVal] = useState("");
  const [cgstVal, setCgstVal] = useState("0");
  const [sgstVal, setSgstVal] = useState("0");
  const [igstVal, setIgstVal] = useState("0");
  const [pos, setPos] = useState("27");
  const [goodsOrServices, setGoodsOrServices] = useState("goods");
  const [isRcm, setIsRcm] = useState(false);

  // Payment Modal State
  const [payRef, setPayRef] = useState("");
  const [payDate, setPayDate] = useState("2026-11-17");
  const [payMode, setPayMode] = useState("NEFT");
  const [payAmount, setPayAmount] = useState("");

  const activeReg = registrations.find((r) => r.id === selectedRegId);

  const loadDocuments = useCallback(async (regId: string, period?: string) => {
    if (!regId) {
      setDocuments([]);
      return;
    }
    setLoading(true);
    setErrorMsg(null);
    try {
      const liveDocs = await listPurchaseDocuments(regId, period || undefined);
      setDocuments(liveDocs.map(mapDocument));
    } catch (err: any) {
      setDocuments([]);
      setErrorMsg(err?.message || "Failed to load purchase register from backend.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const sess = loadSession();
    const targetReg = selectedRegId || sess.registrationId || registrations[0]?.id || "";
    if (targetReg && targetReg !== selectedRegId) {
      setSelectedRegId(targetReg);
    }
    if (targetReg) {
      loadDocuments(targetReg, returnPeriod);
    }
  }, [selectedRegId, returnPeriod, registrations, loadDocuments]);

  const handleCreateDocument = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedRegId) {
      setErrorMsg("Select an active GST registration before creating a purchase document.");
      return;
    }
    setSubmitting(true);
    setErrorMsg(null);
    setStatusMsg(null);
    try {
      const taxable = parseFloat(taxableVal) || 0;
      const cgst = parseFloat(cgstVal) || 0;
      const sgst = parseFloat(sgstVal) || 0;
      const igst = parseFloat(igstVal) || 0;
      const total = taxable + cgst + sgst + igst;
      await createPurchaseDocument({
        registration_id: selectedRegId,
        document_type: docType,
        document_number: docNum.trim(),
        document_date: docDate,
        financial_period: returnPeriod,
        supplier_gstin: supplierGstin.trim() || null,
        supplier_legal_name: supplierName.trim(),
        recipient_gstin: activeReg?.gstin || supplierGstin.trim(),
        place_of_supply: pos,
        is_reverse_charge: isRcm,
        total_taxable_amount: taxable,
        cgst_amount: cgst,
        sgst_amount: sgst,
        igst_amount: igst,
        total_invoice_value: total,
        goods_services_type: goodsOrServices,
        receipt_status: "UNKNOWN",
      });
      setStatusMsg(`Recorded purchase document ${docNum.trim()} in backend database.`);
      setShowAddForm(false);
      setDocNum("");
      setSupplierGstin("");
      setSupplierName("");
      setTaxableVal("");
      setCgstVal("0");
      setSgstVal("0");
      setIgstVal("0");
      await loadDocuments(selectedRegId, returnPeriod);
    } catch (err: any) {
      setErrorMsg(err?.message || "Failed to create purchase document.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleApprove = async (docId: string) => {
    setErrorMsg(null);
    setStatusMsg(null);
    try {
      await approvePurchaseDocument(docId);
      setStatusMsg("Purchase document approved for reconciliation and ITC claim evaluation.");
      await loadDocuments(selectedRegId, returnPeriod);
    } catch (err: any) {
      setErrorMsg(err?.message || "Approval failed.");
    }
  };

  const handleRecordPayment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!showPaymentModal) return;
    setSubmitting(true);
    setErrorMsg(null);
    setStatusMsg(null);
    try {
      const amt = parseFloat(payAmount) || 0;
      await recordPurchasePayment({
        payment_reference: payRef.trim(),
        payment_date: payDate,
        payment_mode: payMode,
        total_amount_paid: amt,
        allocations: [
          {
            purchase_doc_id: showPaymentModal.id,
            allocated_taxable_amount: Math.max(0, amt - showPaymentModal.raw_tax),
            allocated_tax_amount: Math.min(amt, showPaymentModal.raw_tax),
            allocated_total_amount: amt,
          },
        ],
      });
      setStatusMsg(`Allocated payment ${payRef.trim()} against ${showPaymentModal.document_number}.`);
      setShowPaymentModal(null);
      setPayAmount("");
      setPayRef("");
      await loadDocuments(selectedRegId, returnPeriod);
    } catch (err: any) {
      setErrorMsg(err?.message || "Failed to allocate payment.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleVerifyReceipt = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!showReceiptModal) return;
    setSubmitting(true);
    setErrorMsg(null);
    setStatusMsg(null);
    try {
      const isService = showReceiptModal.goods_services_type === "services";
      await verifyPurchaseReceipt(showReceiptModal.id, {
        receipt_status: selectedReceiptStatus,
        ...(selectedReceiptStatus === "DEEMED_RECEIVED"
          ? { deemed_receipt_reason: receiptProofRef.trim(), deemed_receipt_category: "BILL_TO_SHIP_TO" }
          : isService
          ? { service_completion_date: receiptDate, service_acceptance_cert_ref: receiptProofRef.trim() }
          : { goods_receipt_date: receiptDate, goods_receipt_note_ref: receiptProofRef.trim() }),
      });
      setStatusMsg(
        `Section 16(2)(b) receipt status updated to ${selectedReceiptStatus} for ${showReceiptModal.document_number}.`
      );
      setShowReceiptModal(null);
      await loadDocuments(selectedRegId, returnPeriod);
    } catch (err: any) {
      setErrorMsg(err?.message || "Receipt verification failed.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleOpenLotsModal = async (doc: PurchaseDoc) => {
    setShowLotModal(doc);
    setErrorMsg(null);
    try {
      const lots = await listPurchaseLots(doc.id);
      setLotsHistory(lots);
      setLotNum(lots.length + 1);
    } catch (err: any) {
      setLotsHistory([]);
      setErrorMsg(err?.message || "Failed to load lot ledger history.");
    }
  };

  const handleRecordLot = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!showLotModal) return;
    setSubmitting(true);
    setErrorMsg(null);
    try {
      await recordPurchaseLot(showLotModal.id, {
        lot_number: lotNum,
        received_quantity: parseFloat(lotQty) || 0,
        lot_reference: lotRef.trim(),
        receipt_date: receiptDate,
        goods_receipt_note_ref: lotGrn.trim(),
        carrier_name: lotCarrier.trim() || undefined,
        vehicle_number: lotVehicle.trim() || undefined,
        eway_bill_number: lotEway.trim() || undefined,
      });
      const updatedLots = await listPurchaseLots(showLotModal.id);
      setLotsHistory(updatedLots);
      setLotNum(updatedLots.length + 1);
      setStatusMsg(`Lot ${lotNum} recorded in immutable ledger.`);
      await loadDocuments(selectedRegId, returnPeriod);
    } catch (err: any) {
      setErrorMsg(err?.message || "Lot recording failed.");
    } finally {
      setSubmitting(false);
    }
  };

  const unknownReceiptCount = documents.filter((d) => d.receipt_status === "UNKNOWN").length;

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-indigo-600" />
            Independent Purchase Register
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            Buyer books of accounts persisted independently from GSTR-2B. Enforces human approval, Section 16(2)(b) receipt proof, and Rule 37 payment tracking.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {registrations.length > 0 && (
            <select
              value={selectedRegId}
              onChange={(e) => {
                setSelectedRegId(e.target.value);
                saveSession({ registrationId: e.target.value });
              }}
              className="px-3 py-2 border border-slate-300 rounded-lg text-xs font-mono bg-white"
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
            value={returnPeriod}
            onChange={(e) => {
              setReturnPeriod(e.target.value);
              saveSession({ returnPeriod: e.target.value });
            }}
            className="px-3 py-2 border border-slate-300 rounded-lg text-xs font-mono"
          />
          <button
            onClick={() => { if (selectedRegId) loadDocuments(selectedRegId, returnPeriod); }}
            disabled={loading}
            className="px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-semibold flex items-center gap-1"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
          <button
            onClick={() => setShowAddForm(!showAddForm)}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-medium tracking-wide shadow-sm flex items-center gap-2 transition-colors"
          >
            <PlusCircle className="w-4 h-4" />
            {showAddForm ? "Cancel Entry" : "Record Inward Invoice"}
          </button>
        </div>
      </div>

      {errorMsg && (
        <div className="bg-rose-50 border border-rose-200 rounded-xl p-3 text-xs text-rose-800 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}
      {statusMsg && (
        <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-3 text-xs text-emerald-800 flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{statusMsg}</span>
        </div>
      )}

      {/* Statutory Receipt Gate Notice */}
      <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
        <div className="flex items-start gap-3">
          <ShieldCheck className="w-5 h-5 text-amber-700 shrink-0 mt-0.5" />
          <div>
            <h4 className="font-bold text-amber-900 flex items-center gap-2">
              Section 16(2)(b) Statutory Receipt Gate Active
            </h4>
            <p className="text-amber-800 text-[11px] mt-0.5 leading-relaxed">
              Inward records missing verified delivery evidence (GRN/challan date, service acceptance cert, or final lot confirmation) remain blocked from ITC availment until verification is attached.
            </p>
          </div>
        </div>
        <span className="bg-white px-2.5 py-1 rounded border border-amber-300 font-mono text-[11px] font-bold text-amber-900 shrink-0">
          {unknownReceiptCount} Unverified Receipt{unknownReceiptCount === 1 ? "" : "s"}
        </span>
      </div>

      {/* Add Document Drawer / Form */}
      {showAddForm && (
        <form onSubmit={handleCreateDocument} className="bg-slate-50 border border-slate-300 rounded-xl p-6 space-y-4 shadow-inner">
          <h3 className="text-sm font-bold text-slate-800 border-b border-slate-200 pb-2 flex items-center justify-between">
            <span>New Inward Purchase Document</span>
            <span className="text-[11px] font-normal text-indigo-700 bg-indigo-50 px-2 py-0.5 rounded border border-indigo-200">
              PAN: {orgPan || activeReg?.gstin?.slice(2, 12) || "Session Scoped"}
            </span>
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 text-xs">
            <div>
              <label className="block font-semibold text-slate-700 mb-1">GST Registration (Branch)</label>
              <select
                value={selectedRegId}
                onChange={(e) => setSelectedRegId(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
                required
              >
                <option value="">Select registration...</option>
                {registrations.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.gstin} ({r.state_code})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Document Type</label>
              <select
                value={docType}
                onChange={(e) => setDocType(e.target.value)}
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
                onChange={(e) => setSupplierGstin(e.target.value.toUpperCase())}
                placeholder="27ABCDE1234F1Z5"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Supplier Legal Name</label>
              <input
                type="text"
                value={supplierName}
                onChange={(e) => setSupplierName(e.target.value)}
                placeholder="Vendor Legal Name"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Document Number</label>
              <input
                type="text"
                value={docNum}
                onChange={(e) => setDocNum(e.target.value)}
                placeholder="INV-2026-001"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Document Date</label>
              <input
                type="date"
                value={docDate}
                onChange={(e) => setDocDate(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Supply Nature</label>
              <select
                value={goodsOrServices}
                onChange={(e) => setGoodsOrServices(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
              >
                <option value="goods">Goods</option>
                <option value="services">Services</option>
                <option value="capital_goods">Capital Goods</option>
                <option value="both">Mixed / Both</option>
              </select>
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Place of Supply (State)</label>
              <input
                type="text"
                maxLength={2}
                value={pos}
                onChange={(e) => setPos(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Taxable Value (₹)</label>
              <input
                type="number"
                step="0.01"
                value={taxableVal}
                onChange={(e) => setTaxableVal(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">CGST (₹)</label>
              <input
                type="number"
                step="0.01"
                value={cgstVal}
                onChange={(e) => setCgstVal(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">SGST (₹)</label>
              <input
                type="number"
                step="0.01"
                value={sgstVal}
                onChange={(e) => setSgstVal(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">IGST (₹)</label>
              <input
                type="number"
                step="0.01"
                value={igstVal}
                onChange={(e) => setIgstVal(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono focus:ring-2 focus:ring-indigo-500"
              />
            </div>
          </div>

          <div className="flex items-center justify-between pt-2">
            <label className="flex items-center gap-2 text-xs font-medium text-slate-700 cursor-pointer">
              <input
                type="checkbox"
                checked={isRcm}
                onChange={(e) => setIsRcm(e.target.checked)}
                className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
              />
              Subject to Reverse Charge Mechanism (RCM under Sec 9(3)/9(4))
            </label>

            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setShowAddForm(false)}
                className="px-4 py-2 border border-slate-300 rounded-lg text-xs font-medium text-slate-700 hover:bg-slate-100"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-medium shadow-sm"
              >
                {submitting ? "Saving..." : "Save Inward Record"}
              </button>
            </div>
          </div>
        </form>
      )}

      {/* Register Table */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
                <th className="px-4 py-3">Document &amp; Type</th>
                <th className="px-4 py-3">Supplier</th>
                <th className="px-4 py-3 text-center">Sec 16(2)(b) Receipt Gate</th>
                <th className="px-4 py-3 text-right">Taxable (₹)</th>
                <th className="px-4 py-3 text-right">GST (₹)</th>
                <th className="px-4 py-3 text-right font-bold text-slate-900">Invoice Total</th>
                <th className="px-4 py-3 text-center">Approval</th>
                <th className="px-4 py-3 text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 font-mono">
              {documents.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center font-sans text-slate-500">
                    {loading
                      ? "Loading live purchase documents..."
                      : "No purchase documents found for the selected registration and period. Record a new inward invoice above."}
                  </td>
                </tr>
              ) : (
                documents.map((doc) => (
                  <tr key={doc.id} className="hover:bg-slate-50/80 transition-colors">
                    <td className="px-4 py-3">
                      <div className="font-bold text-slate-900 flex items-center gap-1.5">
                        {doc.document_number}
                        {doc.is_reverse_charge && (
                          <span className="text-[10px] bg-purple-100 text-purple-800 px-1.5 py-0.5 rounded font-sans font-semibold">
                            RCM
                          </span>
                        )}
                      </div>
                      <div className="text-[11px] text-slate-500 font-sans mt-0.5">
                        {doc.document_date} • <span className="capitalize">{doc.goods_services_type}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 font-sans">
                      <div className="font-medium text-slate-900">{doc.supplier_legal_name}</div>
                      <div className="text-[11px] font-mono text-slate-500">{doc.supplier_gstin}</div>
                    </td>
                    <td className="px-4 py-3 text-center font-sans">
                      <div className="flex flex-col items-center gap-1">
                        {doc.receipt_status === "RECEIVED" && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 text-[10px] font-semibold border border-emerald-200">
                            <CheckCircle2 className="w-3 h-3" /> RECEIVED
                          </span>
                        )}
                        {doc.receipt_status === "PARTIALLY_RECEIVED" && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-amber-100 text-amber-800 text-[10px] font-semibold border border-amber-200">
                            <Truck className="w-3 h-3" /> PARTIAL LOT
                          </span>
                        )}
                        {doc.receipt_status === "DEEMED_RECEIVED" && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-indigo-100 text-indigo-800 text-[10px] font-semibold border border-indigo-200">
                            <ShieldCheck className="w-3 h-3" /> DEEMED
                          </span>
                        )}
                        {(doc.receipt_status === "UNKNOWN" || doc.receipt_status === "NOT_RECEIVED") && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-rose-100 text-rose-800 text-[10px] font-semibold border border-rose-200">
                            <AlertTriangle className="w-3 h-3" /> {doc.receipt_status}
                          </span>
                        )}
                        {doc.delivery_proof_ref && (
                          <span className="text-[10px] font-mono text-slate-500">{doc.delivery_proof_ref}</span>
                        )}
                        {doc.lot_info && (
                          <span className="text-[10px] font-sans text-amber-700 font-medium">{doc.lot_info}</span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right">{doc.total_taxable_amount}</td>
                    <td className="px-4 py-3 text-right text-slate-600">{doc.tax_amount}</td>
                    <td className="px-4 py-3 text-right font-bold text-slate-900">{doc.total_invoice_value}</td>
                    <td className="px-4 py-3 text-center">
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-sans font-semibold ${
                          doc.status === "approved"
                            ? "bg-emerald-100 text-emerald-800"
                            : doc.status === "pending_approval"
                            ? "bg-amber-100 text-amber-800"
                            : "bg-slate-200 text-slate-700"
                        }`}
                      >
                        {doc.status.replace(/_/g, " ").toUpperCase()}
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
                          setShowReceiptModal(doc);
                          setReceiptProofRef(doc.delivery_proof_ref || "");
                        }}
                        className="px-2 py-1 bg-sky-50 hover:bg-sky-100 text-sky-700 rounded text-[10px] font-medium border border-sky-200"
                      >
                        Receipt Gate
                      </button>
                      {doc.goods_services_type === "goods" && (
                        <button
                          onClick={() => handleOpenLotsModal(doc)}
                          className="px-2 py-1 bg-purple-50 hover:bg-purple-100 text-purple-700 rounded text-[10px] font-medium border border-purple-200"
                        >
                          Lots
                        </button>
                      )}
                      <button
                        onClick={() => {
                          setShowPaymentModal(doc);
                          setPayAmount(String(doc.raw_total));
                        }}
                        className="px-2 py-1 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded text-[10px] font-medium border border-indigo-200"
                      >
                        Pay
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Payment Modal */}
      {showPaymentModal && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-xl shadow-xl border border-slate-200 max-w-md w-full p-6 space-y-4">
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <DollarSign className="w-4 h-4 text-emerald-600" />
              Record Vendor Payment — {showPaymentModal.document_number}
            </h3>
            <form onSubmit={handleRecordPayment} className="space-y-3 text-xs">
              <div>
                <label className="block font-semibold text-slate-700 mb-1">Payment Reference / UTR</label>
                <input
                  type="text"
                  value={payRef}
                  onChange={(e) => setPayRef(e.target.value)}
                  placeholder="UTR / NEFT Reference"
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
                    onChange={(e) => setPayDate(e.target.value)}
                    className="w-full px-3 py-2 border border-slate-300 rounded"
                    required
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Mode</label>
                  <select
                    value={payMode}
                    onChange={(e) => setPayMode(e.target.value)}
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
                  onChange={(e) => setPayAmount(e.target.value)}
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
                  disabled={submitting}
                  className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-xs font-medium"
                >
                  {submitting ? "Saving..." : "Save & Allocate Payment"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Section 16(2)(b) Receipt Modal */}
      {showReceiptModal && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-xl shadow-xl border border-slate-200 max-w-md w-full p-6 space-y-4">
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-sky-600" />
              Verify Receipt — {showReceiptModal.document_number}
            </h3>
            <form onSubmit={handleVerifyReceipt} className="space-y-3 text-xs">
              <div>
                <label className="block font-semibold text-slate-700 mb-1">Receipt Status</label>
                <select
                  value={selectedReceiptStatus}
                  onChange={(e) => setSelectedReceiptStatus(e.target.value as any)}
                  className="w-full px-3 py-2 border border-slate-300 rounded"
                >
                  <option value="RECEIVED">RECEIVED (Full Delivery / Service Completion)</option>
                  <option value="PARTIALLY_RECEIVED">PARTIALLY_RECEIVED (Proviso Hold)</option>
                  <option value="DEEMED_RECEIVED">DEEMED_RECEIVED (Bill-to Ship-to)</option>
                </select>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Receipt / Completion Date</label>
                  <input
                    type="date"
                    value={receiptDate}
                    onChange={(e) => setReceiptDate(e.target.value)}
                    className="w-full px-3 py-2 border border-slate-300 rounded"
                    required
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Proof Reference (GRN / Cert)</label>
                  <input
                    type="text"
                    value={receiptProofRef}
                    onChange={(e) => setReceiptProofRef(e.target.value)}
                    placeholder="GRN-2026-0001"
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
                  disabled={submitting}
                  className="px-3 py-1.5 bg-sky-600 hover:bg-sky-700 text-white rounded text-xs font-medium"
                >
                  {submitting ? "Verifying..." : "Save Receipt Verification"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Lots Ledger Modal */}
      {showLotModal && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-xl shadow-xl border border-slate-200 max-w-lg w-full p-6 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                <Layers className="w-4 h-4 text-purple-600" />
                Goods Receipt Lot Ledger — {showLotModal.document_number}
              </h3>
              <button
                onClick={() => setShowLotModal(null)}
                className="text-slate-400 hover:text-slate-600 text-lg font-bold"
              >
                &times;
              </button>
            </div>

            {lotsHistory.length > 0 ? (
              <div className="border border-slate-200 rounded-lg overflow-hidden text-xs">
                <table className="w-full text-left font-mono">
                  <thead className="bg-slate-50 text-slate-600 font-sans">
                    <tr>
                      <th className="px-2 py-1.5">Lot #</th>
                      <th className="px-2 py-1.5">Ref / GRN</th>
                      <th className="px-2 py-1.5 text-right">Qty</th>
                      <th className="px-2 py-1.5">Final?</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {lotsHistory.map((lot) => (
                      <tr key={lot.id}>
                        <td className="px-2 py-1.5">
                          #{lot.lot_number} of {lot.expected_lots_count}
                        </td>
                        <td className="px-2 py-1.5">
                          {lot.lot_reference} ({lot.goods_receipt_note_ref})
                        </td>
                        <td className="px-2 py-1.5 text-right">{lot.received_quantity}</td>
                        <td className="px-2 py-1.5">{lot.is_final_lot ? "YES" : "NO"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-xs text-slate-500">No lots recorded in ledger yet for this invoice.</p>
            )}

            <form onSubmit={handleRecordLot} className="space-y-3 text-xs border-t border-slate-100 pt-3">
              <h4 className="font-bold text-slate-800">Record Delivery Lot</h4>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Lot Number</label>
                  <input
                    type="number"
                    min={1}
                    value={lotNum}
                    onChange={(e) => setLotNum(parseInt(e.target.value, 10) || 1)}
                    className="w-full px-2.5 py-1.5 border border-slate-300 rounded font-mono"
                    required
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Received Quantity</label>
                  <input
                    type="number"
                    step="0.01"
                    value={lotQty}
                    onChange={(e) => setLotQty(e.target.value)}
                    className="w-full px-2.5 py-1.5 border border-slate-300 rounded font-mono"
                    required
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Lot / Challan Ref</label>
                  <input
                    type="text"
                    value={lotRef}
                    onChange={(e) => setLotRef(e.target.value)}
                    placeholder="DC-LOT-01"
                    className="w-full px-2.5 py-1.5 border border-slate-300 rounded font-mono"
                    required
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">GRN Reference</label>
                  <input
                    type="text"
                    value={lotGrn}
                    onChange={(e) => setLotGrn(e.target.value)}
                    placeholder="GRN-2026-0001"
                    className="w-full px-2.5 py-1.5 border border-slate-300 rounded font-mono"
                    required
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Carrier</label>
                  <input
                    type="text"
                    value={lotCarrier}
                    onChange={(e) => setLotCarrier(e.target.value)}
                    className="w-full px-2.5 py-1.5 border border-slate-300 rounded"
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Vehicle No</label>
                  <input
                    type="text"
                    value={lotVehicle}
                    onChange={(e) => setLotVehicle(e.target.value)}
                    placeholder="MH-12-AB-1234"
                    className="w-full px-2.5 py-1.5 border border-slate-300 rounded font-mono"
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">E-Way Bill No</label>
                  <input
                    type="text"
                    value={lotEway}
                    onChange={(e) => setLotEway(e.target.value)}
                    placeholder="E-Way Bill Reference"
                    className="w-full px-2.5 py-1.5 border border-slate-300 rounded font-mono"
                  />
                </div>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowLotModal(null)}
                  className="px-3 py-1.5 border border-slate-300 rounded text-slate-700 text-xs"
                >
                  Close
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="px-3 py-1.5 bg-purple-600 hover:bg-purple-700 text-white rounded text-xs font-medium"
                >
                  {submitting ? "Recording..." : "Record Lot in Immutable Ledger"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default PurchaseRegister;
