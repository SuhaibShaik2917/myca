import React, { useState } from "react";
import {
  AlertOctagon,
  CheckCircle2,
  Clock,
  ShieldCheck,
  FileText,
  DollarSign,
  Calendar,
  Info,
  Download,
  ExternalLink,
  ChevronRight,
  X,
  AlertTriangle,
  HelpCircle
} from "lucide-react";

interface ProvenanceItem {
  docNo: string;
  docDate: string;
  supplierGstin: string;
  supplierName: string;
  taxable: string;
  cgst: string;
  sgst: string;
  igst: string;
  cess: string;
  total: string;
  ruleCode?: string;
  priorPeriod?: string;
  isAvailedIn3B?: boolean;
  interestBasis?: string;
}

interface Table4DrillDown {
  sectionCode: string;
  title: string;
  items: ProvenanceItem[];
}

export const ItcRiskDashboard: React.FC = () => {
  const [returnPeriod, setReturnPeriod] = useState("2026-11");
  const [drillDownModal, setDrillDownModal] = useState<Table4DrillDown | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  // Summary Metrics (4 Non-Confused Statutory Subtotals)
  const statutorySubtotals = {
    portalAvailable: "4,82,500.00",
    booksMatched: "4,64,500.00",
    legallyEligible: "3,54,600.00",
    humanApproved: "3,54,600.00"
  };

  const rule37AgingRows = [
    {
      docNo: "INV-2026-088",
      supplier: "Alpha Heavy Fabricators",
      invoiceDate: "2026-04-10",
      daysElapsed: 221,
      invoiceValue: "2,36,000.00",
      unpaidAmount: "1,18,000.00",
      reversalRequired: "18,000.00",
      interestExposure: "1,980.00",
      status: "REVERSAL_TRIGGERED",
      priorAvailedPeriod: "2026-04",
      isReverseCharge: false,
      interestCondition: "Conditional under Sec 50(3) read with Rule 88B(3): Nil if ECL balance >= reversal amount."
    },
    {
      docNo: "SRV-5512",
      supplier: "Delta Facility Management",
      invoiceDate: "2026-06-25",
      daysElapsed: 145,
      invoiceValue: "88,500.00",
      unpaidAmount: "88,500.00",
      reversalRequired: "0.00",
      interestExposure: "0.00",
      status: "35_DAYS_REMAINING",
      priorAvailedPeriod: "2026-06",
      isReverseCharge: false,
      interestCondition: "No reversal due yet (under 180 days)."
    }
  ];

  const rule37ARows = [
    {
      supplierGstin: "27AAACR1234Q1Z2",
      supplierName: "Reliance Polymers Dist",
      gstr1Period: "2026-02",
      supplierGstr3bStatus: "NOT_FILED",
      supplierCutoff: "30-Sep-2026",
      reversalDeadline: "30-Nov-2026",
      itcExposure: "32,400.00",
      interestRate: "18% p.a. past 30-Nov",
      actionNeeded: "Reverse in Nov 2026 GSTR-3B under Table 4(B)(2)",
      statutoryReference: "Rule 37A proviso: Zero interest if reversed on or before 30th November. Section 50(3) interest applies only after 30-Nov subject to ECL utilization."
    }
  ];

  const blockedCreditRows = [
    {
      docNo: "VEH-2026-01",
      supplier: "Sai Motors Dealership",
      section: "Section 17(5)(a)",
      description: "Motor Vehicles for employee transport (< 13 seating)",
      blockedAmount: "65,000.00",
      status: "BLOCKED_CONFIRMED"
    },
    {
      docNo: "CAT-NOV-12",
      supplier: "Saffron Caterers & Hospitality",
      section: "Section 17(5)(b)",
      description: "Outdoor catering for annual company celebration",
      blockedAmount: "12,500.00",
      status: "BLOCKED_CONFIRMED"
    }
  ];

  // Inward documents communicated in GSTR-2B but unbooked in Buyer's Purchase Register
  // Explains why GSTR-2B Total (₹4,82,500) > Books Matched Total (₹4,64,500)
  const unbookedGstr2bDocuments = [
    {
      docNo: "SUP-2B-998",
      docDate: "2026-11-08",
      supplierGstin: "27AAACR1234Q1Z2",
      supplierName: "Reliance Polymers Dist",
      taxable: "1,00,000.00",
      igst: "18,000.00",
      cgst: "0.00",
      sgst: "0.00",
      cess: "0.00",
      totalTax: "18,000.00",
      varianceCategory: "MISSING_IN_BOOKS",
      statutoryReason: "Document reported by supplier in GSTR-1, but inward goods/services not yet entered into Purchase Register or verified by Stores. Retained outside Table 4(A)(5) until booked."
    }
  ];

  // Table 4 Line Item Provenance Map for Drill-down
  const provenanceMap: Record<string, Table4DrillDown> = {
    "4A1": {
      sectionCode: "Table 4(A)(1)",
      title: "Import of Goods (ICEGATE Communications)",
      items: [
        {
          docNo: "BOE-882190",
          docDate: "2026-11-02",
          supplierGstin: "INBOM4",
          supplierName: "Customs Bombay Port (ICEGATE)",
          taxable: "3,33,333.33",
          cgst: "0.00",
          sgst: "0.00",
          igst: "60,000.00",
          cess: "0.00",
          total: "60,000.00",
          ruleCode: "SEC_16_2_ICEGATE",
          isAvailedIn3B: true
        }
      ]
    },
    "4A3": {
      sectionCode: "Table 4(A)(3)",
      title: "Inward Supplies Attracting Reverse Charge (RCM)",
      items: [
        {
          docNo: "GTA-NOV-01",
          docDate: "2026-11-05",
          supplierGstin: "27AAGFG4411D1ZA",
          supplierName: "FastTrack Logistics (Goods Transport)",
          taxable: "1,11,111.11",
          cgst: "10,000.00",
          sgst: "10,000.00",
          igst: "10,000.00",
          cess: "0.00",
          total: "30,000.00",
          ruleCode: "SEC_9_3_RCM_GTA",
          isAvailedIn3B: true
        }
      ]
    },
    "4A4": {
      sectionCode: "Table 4(A)(4)",
      title: "Inward Supplies from Input Service Distributor (ISD)",
      items: [
        {
          docNo: "ISD-INV-44",
          docDate: "2026-11-07",
          supplierGstin: "27AAACZ1122C1Z8",
          supplierName: "Apex Corporate HQ (ISD Registration)",
          taxable: "1,41,666.67",
          cgst: "5,250.00",
          sgst: "5,250.00",
          igst: "15,000.00",
          cess: "0.00",
          total: "25,500.00",
          ruleCode: "SEC_20_ISD_DISTRIBUTION",
          isAvailedIn3B: true
        }
      ]
    },
    "4A5": {
      sectionCode: "Table 4(A)(5)",
      title: "All Other ITC (Inward B2B Supplies + Table 4(D)(1) Reclaim)",
      items: [
        {
          docNo: "INV-2026-101",
          docDate: "2026-11-04",
          supplierGstin: "27AAGCB1234F1ZQ",
          supplierName: "Bharat Industrial Spares Ltd",
          taxable: "11,11,111.11",
          cgst: "1,00,000.00",
          sgst: "1,00,000.00",
          igst: "0.00",
          cess: "0.00",
          total: "2,00,000.00",
          ruleCode: "SEC_16_ELIGIBLE",
          isAvailedIn3B: true
        },
        {
          docNo: "INV-2026-105",
          docDate: "2026-11-06",
          supplierGstin: "29BBBPB9988H1Z4",
          supplierName: "Karnataka Precision Tools",
          taxable: "6,66,666.67",
          cgst: "0.00",
          sgst: "0.00",
          igst: "1,20,000.00",
          cess: "0.00",
          total: "1,20,000.00",
          ruleCode: "SEC_16_ELIGIBLE",
          isAvailedIn3B: true
        },
        {
          docNo: "VEH-2026-01",
          docDate: "2026-11-03",
          supplierGstin: "27AAACM4433E1Z2",
          supplierName: "Sai Motors Dealership",
          taxable: "3,61,111.11",
          cgst: "0.00",
          sgst: "0.00",
          igst: "65,000.00",
          cess: "0.00",
          total: "65,000.00",
          ruleCode: "SEC_17_5_MOTOR (Included in 4(A) & reversed in 4(B)(1))",
          isAvailedIn3B: true
        },
        {
          docNo: "CAT-NOV-12",
          docDate: "2026-11-05",
          supplierGstin: "27AAACS8899K1Z5",
          supplierName: "Saffron Caterers & Hospitality",
          taxable: "69,444.44",
          cgst: "6,250.00",
          sgst: "6,250.00",
          igst: "0.00",
          cess: "0.00",
          total: "12,500.00",
          ruleCode: "SEC_17_5_FOOD (Included in 4(A) & reversed in 4(B)(1))",
          isAvailedIn3B: true
        },
        {
          docNo: "AMD/9912",
          docDate: "2026-11-06",
          supplierGstin: "27BBBEE5566K1Z1",
          supplierName: "Western Hardware Supplies",
          taxable: "95,833.33",
          cgst: "8,625.00",
          sgst: "8,625.00",
          igst: "0.00",
          cess: "0.00",
          total: "17,250.00",
          ruleCode: "SEC_16_ELIGIBLE",
          isAvailedIn3B: true
        },
        {
          docNo: "REC-R37-01",
          docDate: "2026-11-12",
          supplierGstin: "27AAACH1234D1Z9",
          supplierName: "Zenith Tech Systems (Re-claim on Payment)",
          taxable: "55,555.56",
          cgst: "8,625.00",
          sgst: "8,625.00",
          igst: "0.00",
          cess: "0.00",
          total: "17,250.00",
          ruleCode: "RULE_37_REAVAILMENT (Included in 4(A)(5) and reported in 4(D)(1))",
          priorPeriod: "2026-05",
          isAvailedIn3B: true
        }
      ]
    },
    "4B1": {
      sectionCode: "Table 4(B)(1)",
      title: "Non-Reclaimable Reversals (Rules 38, 42, 43 and Section 17(5))",
      items: [
        {
          docNo: "VEH-2026-01",
          docDate: "2026-11-03",
          supplierGstin: "27AAACM4433E1Z2",
          supplierName: "Sai Motors Dealership",
          taxable: "3,61,111.11",
          cgst: "0.00",
          sgst: "0.00",
          igst: "65,000.00",
          cess: "0.00",
          total: "65,000.00",
          ruleCode: "SEC_17_5_MOTOR",
          isAvailedIn3B: true
        },
        {
          docNo: "CAT-NOV-12",
          docDate: "2026-11-05",
          supplierGstin: "27AAACS8899K1Z5",
          supplierName: "Saffron Caterers & Hospitality",
          taxable: "69,444.44",
          cgst: "6,250.00",
          sgst: "6,250.00",
          igst: "0.00",
          cess: "0.00",
          total: "12,500.00",
          ruleCode: "SEC_17_5_FOOD",
          isAvailedIn3B: true
        }
      ]
    },
    "4B2": {
      sectionCode: "Table 4(B)(2)",
      title: "Reclaimable Reversals (Rule 37 180-Day & Rule 37A Supplier Non-Filing)",
      items: [
        {
          docNo: "INV-2026-088",
          docDate: "2026-04-10",
          supplierGstin: "27AABCA9911F1ZZ",
          supplierName: "Alpha Heavy Fabricators",
          taxable: "1,00,000.00",
          cgst: "9,000.00",
          sgst: "9,000.00",
          igst: "0.00",
          cess: "0.00",
          total: "18,000.00",
          ruleCode: "RULE_37_180_DAYS",
          priorPeriod: "2026-04",
          isAvailedIn3B: true,
          interestBasis: "18% p.a. from 2026-04 availment date. Conditional upon ECL utilization."
        },
        {
          docNo: "PUR-2026-041",
          docDate: "2026-02-14",
          supplierGstin: "27AAACR1234Q1Z2",
          supplierName: "Reliance Polymers Dist",
          taxable: "1,80,000.00",
          cgst: "0.00",
          sgst: "0.00",
          igst: "32,400.00",
          cess: "0.00",
          total: "32,400.00",
          ruleCode: "RULE_37A_NON_FILING",
          priorPeriod: "2026-02",
          isAvailedIn3B: true,
          interestBasis: "Reversed on or before 30-Nov-2026 -> Nil interest under Rule 37A proviso."
        }
      ]
    },
    "4D1": {
      sectionCode: "Table 4(D)(1)",
      title: "ITC Reclaimed from Prior Period Table 4(B)(2) Reversals",
      items: [
        {
          docNo: "INV-2026-015",
          docDate: "2026-03-20",
          supplierGstin: "27AAACH1234D1Z9",
          supplierName: "Zenith Tech Systems (Vendor Paid on 2026-11-12)",
          taxable: "55,555.56",
          cgst: "0.00",
          sgst: "0.00",
          igst: "0.00",
          cess: "0.00",
          total: "0.00",
          ruleCode: "RECLAIM_RULE_37",
          priorPeriod: "2026-05 (Reversed) -> 2026-11 (Reclaimed)",
          isAvailedIn3B: true
        }
      ]
    },
    "4D2": {
      sectionCode: "Table 4(D)(2)",
      title: "Ineligible ITC from GSTR-2B Unavailable (Section 16(4) / PoS)",
      items: [
        {
          docNo: "EXP-2025-99",
          docDate: "2025-08-15",
          supplierGstin: "06AAACD1111C1Z3",
          supplierName: "Haryana Logistics Hub",
          taxable: "68,888.89",
          cgst: "0.00",
          sgst: "0.00",
          igst: "12,400.00",
          cess: "0.00",
          total: "12,400.00",
          ruleCode: "SEC_16_4_TIME_BARRED",
          isAvailedIn3B: false
        }
      ]
    }
  };

  const handleExportAuditReport = () => {
    setIsExporting(true);
    // Simulate API export call to /api/v1/reconciliation/export/audit-report
    setTimeout(() => {
      setIsExporting(false);
      const auditPayload = {
        organization_id: "demo-tenant-id",
        return_period: returnPeriod,
        generated_at: new Date().toISOString(),
        table_4_totals: statutorySubtotals,
        unbooked_gstr2b_proof: unbookedGstr2bDocuments,
        reversals_ledger: provenanceMap["4B2"].items,
        statutory_compliance_status: "VERIFIED_AUDIT_READY"
      };
      const blob = new Blob([JSON.stringify(auditPayload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `ITC_Reconciliation_Audit_Report_${returnPeriod}.json`;
      a.click();
      URL.revokeObjectURL(url);
    }, 600);
  };

  return (
    <div className="space-y-6">
      {/* Top Header with Period & Export Audit Report Button */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
        <div>
          <h1 className="text-base font-bold text-slate-900 flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-indigo-600" />
            Statutory ITC Risk, Reversals & Form GSTR-3B Table 4 Ledger
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Full invoice-level provenance, Rule 37/37A statutory aging, Section 50(3) interest condition & unbooked reconciliation proof.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-xs">
            <Calendar className="w-4 h-4 text-slate-400" />
            <select
              value={returnPeriod}
              onChange={e => setReturnPeriod(e.target.value)}
              className="px-2.5 py-1.5 bg-slate-50 border border-slate-300 rounded-lg text-slate-700 font-mono text-xs font-semibold"
            >
              <option value="2026-11">2026-11 (November 2026)</option>
              <option value="2026-10">2026-10 (October 2026)</option>
              <option value="2026-09">2026-09 (September 2026)</option>
            </select>
          </div>
          <button
            onClick={handleExportAuditReport}
            disabled={isExporting}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-medium tracking-wide shadow-sm flex items-center gap-2 transition-colors shrink-0"
          >
            <Download className="w-4 h-4" />
            {isExporting ? "Exporting Audit..." : "Export Audit Report"}
          </button>
        </div>
      </div>

      {/* 4 Distinct Non-Confused Subtotals Cards */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-bold text-slate-800 uppercase tracking-wider flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600" />
            Statutory ITC Tally & Distinct Non-Confused Subtotals ({returnPeriod})
          </h2>
          <span className="text-xs text-slate-500 font-mono">Ledger Engine v2.2</span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
            <p className="text-[11px] font-semibold text-slate-500 uppercase">1. Portal Available (GSTR-2B)</p>
            <p className="text-xl font-bold font-mono text-indigo-700 mt-1">₹{statutorySubtotals.portalAvailable}</p>
            <p className="text-[11px] text-slate-500 mt-1">Part A communicated by suppliers in GSTR-1/IFF</p>
          </div>

          <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
            <p className="text-[11px] font-semibold text-slate-500 uppercase">2. Books Matched Total</p>
            <p className="text-xl font-bold font-mono text-cyan-700 mt-1">₹{statutorySubtotals.booksMatched}</p>
            <p className="text-[11px] text-slate-500 mt-1">Reconciled inward purchase register records</p>
          </div>

          <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
            <p className="text-[11px] font-semibold text-slate-500 uppercase">3. Legally Eligible ITC</p>
            <p className="text-xl font-bold font-mono text-emerald-700 mt-1">₹{statutorySubtotals.legallyEligible}</p>
            <p className="text-[11px] text-slate-500 mt-1">After Sec 16(2), Sec 17(5) and Rule 37 checks</p>
          </div>

          <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
            <p className="text-[11px] font-semibold text-slate-500 uppercase">4. Human Approved Total</p>
            <p className="text-xl font-bold font-mono text-amber-700 mt-1">₹{statutorySubtotals.humanApproved}</p>
            <p className="text-[11px] text-slate-500 mt-1">Final auditor signed-off safe claim amount</p>
          </div>
        </div>
      </div>

      {/* Variance Explanation: Unbooked GSTR-2B Documents Proof */}
      <div className="bg-amber-50/60 border border-amber-200 rounded-xl p-5 space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-2 border-b border-amber-200">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0" />
            <div>
              <h3 className="text-sm font-bold text-amber-900">
                GSTR-2B vs Books Variance Proof: Unbooked Inward Documents
              </h3>
              <p className="text-xs text-amber-800">
                Portal Total (₹4,82,500.00) exceeds Books Matched Total (₹4,64,500.00) by <strong>₹18,000.00</strong>.
              </p>
            </div>
          </div>
          <span className="text-xs bg-amber-100 text-amber-900 border border-amber-300 px-2.5 py-1 rounded font-semibold shrink-0">
            Missing in Books: ₹18,000.00
          </span>
        </div>

        <p className="text-xs text-amber-900/90 leading-relaxed">
          <strong>Statutory Variance Proof:</strong> This ₹18,000 difference is completely explained by inward supplies communicated in Form GSTR-2B that the recipient has not yet booked or approved in its purchase register.
          <em className="block mt-1 text-amber-800">
            Note: This variance is completely distinct from Rule 37 and Rule 37A reversals, which are assessed exclusively against previously availed historical purchase documents.
          </em>
        </p>

        <div className="bg-white rounded-lg border border-amber-200 overflow-hidden">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-amber-100/50 text-amber-900 font-sans font-semibold">
              <tr>
                <th className="px-3 py-2">Document No & Date</th>
                <th className="px-3 py-2">Supplier GSTIN & Legal Name</th>
                <th className="px-3 py-2 text-right">Taxable Value (₹)</th>
                <th className="px-3 py-2 text-right">IGST (₹)</th>
                <th className="px-3 py-2 text-right">CGST / SGST (₹)</th>
                <th className="px-3 py-2 text-right font-bold">Total ITC (₹)</th>
                <th className="px-3 py-2 text-center font-sans">Statutory Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-amber-100 text-slate-800">
              {unbookedGstr2bDocuments.map((doc, idx) => (
                <tr key={idx} className="hover:bg-amber-50/40">
                  <td className="px-3 py-2">
                    <span className="font-bold text-slate-900">{doc.docNo}</span>
                    <div className="text-[10px] text-slate-500 font-sans">{doc.docDate}</div>
                  </td>
                  <td className="px-3 py-2 font-sans">
                    <div className="font-medium text-slate-900">{doc.supplierName}</div>
                    <div className="text-[10px] text-slate-500 font-mono">{doc.supplierGstin}</div>
                  </td>
                  <td className="px-3 py-2 text-right">₹{doc.taxable}</td>
                  <td className="px-3 py-2 text-right">₹{doc.igst}</td>
                  <td className="px-3 py-2 text-right">₹{doc.cgst}</td>
                  <td className="px-3 py-2 text-right font-bold text-amber-800">₹{doc.totalTax}</td>
                  <td className="px-3 py-2 text-center font-sans">
                    <span className="px-2 py-0.5 rounded text-[10px] bg-amber-100 text-amber-800 font-semibold border border-amber-200">
                      UNBOOKED (HOLD CLAIM)
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Rule 37: 180-Day Vendor Payment Tracking */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-3 border-b border-slate-100 gap-2">
          <div>
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <Clock className="w-4 h-4 text-amber-600" />
              Rule 37: 180-Day Vendor Payment Aging & Proportionate Reversals
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Mandatory reversal under Section 16(2) 2nd Proviso on unpaid vendor invoices after 180 days. Does not apply to Reverse-Charge Supplies or unavailed invoices.
            </p>
          </div>
          <span className="px-2.5 py-1 bg-amber-50 text-amber-800 border border-amber-200 rounded text-xs font-semibold self-start sm:self-auto">
            Sec 16(2) 2nd Proviso
          </span>
        </div>

        {/* Section 50(3) Interest Utilization Notice */}
        <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg text-xs text-blue-900 flex items-start gap-2">
          <Info className="w-4 h-4 text-blue-600 shrink-0 mt-0.5" />
          <div>
            <strong>Section 50(3) Read with Rule 88B(3) Interest Utilization Rule:</strong> Interest on wrongly availed ITC is payable <em>only if and to the extent that</em> the credit was actually debited/utilized against output tax liability. If the recipient continuously maintained an unutilized Electronic Credit Ledger balance exceeding the reversed amount, interest exposure is <strong>Nil (₹0.00)</strong>.
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
              <tr>
                <th className="px-3 py-2">Invoice & Supplier</th>
                <th className="px-3 py-2">Invoice Date</th>
                <th className="px-3 py-2 text-center">Days Elapsed</th>
                <th className="px-3 py-2 text-right">Invoice Value (₹)</th>
                <th className="px-3 py-2 text-right">Unpaid Amount (₹)</th>
                <th className="px-3 py-2 text-right">Reversal Required (₹)</th>
                <th className="px-3 py-2 text-right">Interest Liability (Sec 50)</th>
                <th className="px-3 py-2 text-center">Statutory Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 font-mono">
              {rule37AgingRows.map((r, i) => (
                <tr key={i} className="hover:bg-slate-50">
                  <td className="px-3 py-2.5">
                    <div className="font-bold text-slate-900">{r.docNo}</div>
                    <div className="text-[11px] text-slate-500 font-sans">{r.supplier}</div>
                    <div className="text-[10px] text-slate-400 font-sans">Availed in GSTR-3B: {r.priorAvailedPeriod}</div>
                  </td>
                  <td className="px-3 py-2.5 font-sans">{r.invoiceDate}</td>
                  <td className="px-3 py-2.5 text-center font-bold text-slate-800">{r.daysElapsed} days</td>
                  <td className="px-3 py-2.5 text-right font-medium">₹{r.invoiceValue}</td>
                  <td className="px-3 py-2.5 text-right font-bold text-rose-600">₹{r.unpaidAmount}</td>
                  <td className="px-3 py-2.5 text-right font-bold text-amber-600">₹{r.reversalRequired}</td>
                  <td className="px-3 py-2.5 text-right font-bold text-rose-700">
                    <div>₹{r.interestExposure}</div>
                    <div className="text-[9px] text-slate-500 font-sans font-normal">(if utilized)</div>
                  </td>
                  <td className="px-3 py-2.5 text-center font-sans">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                      r.daysElapsed > 180 ? 'bg-rose-100 text-rose-800' : 'bg-amber-100 text-amber-800'
                    }`}>
                      {r.daysElapsed > 180 ? 'REVERSAL DUE' : 'APPROACHING 180D'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Two Column Grid: Rule 37A & Section 17(5) Blocked Credits */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Rule 37A Supplier GSTR-3B Non-Filing */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-5 space-y-3">
          <div className="flex items-center justify-between pb-2 border-b border-slate-100">
            <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider flex items-center gap-1.5">
              <AlertOctagon className="w-4 h-4 text-rose-600" />
              Rule 37A: Supplier Non-Filing Risk (Dual Deadlines)
            </h3>
            <span className="text-[11px] text-rose-600 font-semibold font-mono">Cut-off: 30-Sep | Reversal: 30-Nov</span>
          </div>
          <p className="text-[11px] text-slate-500">
            Supplier reported in GSTR-1 but failed to furnish Form GSTR-3B by 30th September. ITC must be reversed by 30th November.
          </p>
          <div className="p-2.5 bg-emerald-50 border border-emerald-200 rounded text-[11px] text-emerald-900">
            <strong>Rule 37A Proviso:</strong> Reversal on or before 30th November attracts <strong>NO interest</strong>. Interest under Section 50(3) applies only past 30-Nov and is conditional on credit utilization.
          </div>
          <div className="border border-slate-200 rounded-lg overflow-hidden">
            <table className="w-full text-left text-xs font-mono">
              <thead className="bg-slate-50 text-slate-600 text-[11px]">
                <tr>
                  <th className="px-2.5 py-1.5 font-semibold">Supplier GSTIN</th>
                  <th className="px-2.5 py-1.5 text-center font-semibold">3B Status</th>
                  <th className="px-2.5 py-1.5 text-center font-semibold">Statutory Deadlines</th>
                  <th className="px-2.5 py-1.5 text-right font-semibold">ITC at Risk (₹)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-[11px]">
                {rule37ARows.map((row, idx) => (
                  <tr key={idx} className="hover:bg-slate-50">
                    <td className="px-2.5 py-2 font-sans font-medium text-slate-800">
                      <div>{row.supplierName}</div>
                      <div className="text-[10px] text-slate-500 font-mono">{row.supplierGstin}</div>
                    </td>
                    <td className="px-2.5 py-2 text-center">
                      <span className="px-1.5 py-0.5 rounded text-[10px] bg-rose-100 text-rose-800 font-bold">
                        {row.supplierGstr3bStatus}
                      </span>
                    </td>
                    <td className="px-2.5 py-2 text-center font-sans text-[10px]">
                      <div className="text-amber-700 font-medium">Cutoff: {row.supplierCutoff}</div>
                      <div className="text-rose-700 font-bold">Reversal: {row.reversalDeadline}</div>
                    </td>
                    <td className="px-2.5 py-2 text-right font-bold text-rose-700">₹{row.itcExposure}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Section 17(5) Blocked Credits */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-5 space-y-3">
          <div className="flex items-center justify-between pb-2 border-b border-slate-100">
            <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider flex items-center gap-1.5">
              <AlertOctagon className="w-4 h-4 text-purple-600" />
              Section 17(5): Statutory Blocked Credits
            </h3>
            <span className="text-[11px] text-purple-700 font-semibold font-mono">Permanent Reversals</span>
          </div>
          <p className="text-[11px] text-slate-500">
            Statutorily blocked items (motor vehicles, food/beverages, works contract). Reported in Table 4(A) and permanently reversed under Table 4(B)(1).
          </p>
          <div className="border border-slate-200 rounded-lg overflow-hidden">
            <table className="w-full text-left text-xs font-mono">
              <thead className="bg-slate-50 text-slate-600 text-[11px]">
                <tr>
                  <th className="px-2.5 py-1.5 font-semibold">Clause & Description</th>
                  <th className="px-2.5 py-1.5 text-right font-semibold">Blocked (₹)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-[11px]">
                {blockedCreditRows.map((row, idx) => (
                  <tr key={idx} className="hover:bg-slate-50">
                    <td className="px-2.5 py-2 font-sans">
                      <span className="font-bold text-purple-700">{row.section}:</span>{" "}
                      <span className="text-slate-700">{row.description}</span>
                    </td>
                    <td className="px-2.5 py-2 text-right font-bold text-purple-800">₹{row.blockedAmount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* GSTR-3B Table 4 Working Summary Output with Component Columns & Drill-Down Click */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-3 border-b border-slate-100 gap-2">
          <div>
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <FileText className="w-4 h-4 text-indigo-600" />
              Form GSTR-3B Table 4 Inward ITC Working Summary ({returnPeriod})
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Click any row to drill down into invoice-level provenance and statutory audit trail.
            </p>
          </div>
          <span className="text-xs text-indigo-700 bg-indigo-50 border border-indigo-200 px-2.5 py-1 rounded-full font-medium self-start sm:self-auto">
            Click Rows for Audit Drill-Down
          </span>
        </div>

        <div className="border border-slate-200 rounded-lg overflow-hidden text-xs font-mono">
          <table className="w-full text-left">
            <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold font-sans">
              <tr>
                <th className="px-4 py-2.5">GSTR-3B Table 4 Section</th>
                <th className="px-4 py-2.5 text-right">Integrated Tax (IGST)</th>
                <th className="px-4 py-2.5 text-right">Central Tax (CGST)</th>
                <th className="px-4 py-2.5 text-right">State/UT Tax (SGST)</th>
                <th className="px-4 py-2.5 text-right">Cess Amount</th>
                <th className="px-4 py-2.5 text-right font-bold">Total ITC (₹)</th>
                <th className="px-4 py-2.5 text-center font-sans">Drill-Down</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {/* 4(A) ITC Available */}
              <tr className="bg-emerald-50/40 font-sans font-bold text-slate-800">
                <td colSpan={7} className="px-4 py-2 text-[11px] text-emerald-800">
                  (A) ITC Available (whether in full or in part)
                </td>
              </tr>
              <tr
                onClick={() => setDrillDownModal(provenanceMap["4A1"])}
                className="hover:bg-slate-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">(1) Import of Goods (ICEGATE)</td>
                <td className="px-4 py-2 text-right">60,000.00</td>
                <td className="px-4 py-2 text-right">0.00</td>
                <td className="px-4 py-2 text-right">0.00</td>
                <td className="px-4 py-2 text-right">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-slate-900">60,000.00</td>
                <td className="px-4 py-2 text-center text-indigo-600 font-sans"><ChevronRight className="w-4 h-4 mx-auto" /></td>
              </tr>
              <tr
                onClick={() => setDrillDownModal(provenanceMap["4A3"])}
                className="hover:bg-slate-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">(3) Inward Supplies Attracting Reverse Charge (RCM)</td>
                <td className="px-4 py-2 text-right">10,000.00</td>
                <td className="px-4 py-2 text-right">10,000.00</td>
                <td className="px-4 py-2 text-right">10,000.00</td>
                <td className="px-4 py-2 text-right">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-slate-900">30,000.00</td>
                <td className="px-4 py-2 text-center text-indigo-600 font-sans"><ChevronRight className="w-4 h-4 mx-auto" /></td>
              </tr>
              <tr
                onClick={() => setDrillDownModal(provenanceMap["4A4"])}
                className="hover:bg-slate-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">(4) Inward Supplies from ISD</td>
                <td className="px-4 py-2 text-right">15,000.00</td>
                <td className="px-4 py-2 text-right">5,250.00</td>
                <td className="px-4 py-2 text-right">5,250.00</td>
                <td className="px-4 py-2 text-right">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-slate-900">25,500.00</td>
                <td className="px-4 py-2 text-center text-indigo-600 font-sans"><ChevronRight className="w-4 h-4 mx-auto" /></td>
              </tr>
              <tr
                onClick={() => setDrillDownModal(provenanceMap["4A5"])}
                className="hover:bg-slate-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">
                  (5) All Other ITC (Registered Regular B2B + Prior Reclaims)
                </td>
                <td className="px-4 py-2 text-right">1,20,000.00</td>
                <td className="px-4 py-2 text-right">1,23,500.00</td>
                <td className="px-4 py-2 text-right">1,23,500.00</td>
                <td className="px-4 py-2 text-right">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-slate-900">3,67,000.00</td>
                <td className="px-4 py-2 text-center text-indigo-600 font-sans"><ChevronRight className="w-4 h-4 mx-auto" /></td>
              </tr>

              {/* 4(B) ITC Reversed */}
              <tr className="bg-amber-50/40 font-sans font-bold text-slate-800">
                <td colSpan={7} className="px-4 py-2 text-[11px] text-amber-800">
                  (B) ITC Reversed
                </td>
              </tr>
              <tr
                onClick={() => setDrillDownModal(provenanceMap["4B1"])}
                className="hover:bg-slate-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">
                  (1) Rules 38, 42, 43 and Section 17(5) (Permanent Non-Reclaimable Reversals)
                </td>
                <td className="px-4 py-2 text-right text-rose-600">65,000.00</td>
                <td className="px-4 py-2 text-right text-rose-600">6,250.00</td>
                <td className="px-4 py-2 text-right text-rose-600">6,250.00</td>
                <td className="px-4 py-2 text-right text-rose-600">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-rose-700">77,500.00</td>
                <td className="px-4 py-2 text-center text-indigo-600 font-sans"><ChevronRight className="w-4 h-4 mx-auto" /></td>
              </tr>
              <tr
                onClick={() => setDrillDownModal(provenanceMap["4B2"])}
                className="hover:bg-slate-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">
                  (2) Others: Rule 37 (180-Day) + Rule 37A (Supplier Non-Filing) (Reclaimable)
                </td>
                <td className="px-4 py-2 text-right text-rose-600">32,400.00</td>
                <td className="px-4 py-2 text-right text-rose-600">9,000.00</td>
                <td className="px-4 py-2 text-right text-rose-600">9,000.00</td>
                <td className="px-4 py-2 text-right text-rose-600">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-rose-700">50,400.00</td>
                <td className="px-4 py-2 text-center text-indigo-600 font-sans"><ChevronRight className="w-4 h-4 mx-auto" /></td>
              </tr>

              {/* 4(C) Net ITC */}
              <tr className="bg-indigo-50 font-sans font-bold text-slate-900">
                <td className="px-4 py-2.5">
                  (C) Net ITC Available (A) - (B) [4(A) ₹4,82,500 - 4(B)(1) ₹77,500 - 4(B)(2) ₹50,400]
                </td>
                <td className="px-4 py-2.5 text-right font-mono">1,07,600.00</td>
                <td className="px-4 py-2.5 text-right font-mono">1,23,500.00</td>
                <td className="px-4 py-2.5 text-right font-mono">1,23,500.00</td>
                <td className="px-4 py-2.5 text-right font-mono">0.00</td>
                <td className="px-4 py-2.5 text-right font-mono text-indigo-700 text-sm font-extrabold">3,54,600.00</td>
                <td className="px-4 py-2.5 text-center font-sans text-xs text-slate-400">Calculated</td>
              </tr>

              {/* 4(D) Other Details */}
              <tr className="bg-purple-50/40 font-sans font-bold text-slate-800">
                <td colSpan={7} className="px-4 py-2 text-[11px] text-purple-800">
                  (D) Other Details
                </td>
              </tr>
              <tr
                onClick={() => setDrillDownModal(provenanceMap["4D1"])}
                className="hover:bg-slate-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">
                  (1) ITC reclaimed which was reversed under Table 4(B)(2) in earlier tax period (Reclaimable only)
                </td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-purple-900">0.00</td>
                <td className="px-4 py-2 text-center text-indigo-600 font-sans"><ChevronRight className="w-4 h-4 mx-auto" /></td>
              </tr>
              <tr
                onClick={() => setDrillDownModal(provenanceMap["4D2"])}
                className="hover:bg-slate-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">
                  (2) Ineligible ITC under section 16(4) & PoS restrictions (from GSTR-2B unavailable; never added to 4(A))
                </td>
                <td className="px-4 py-2 text-right text-purple-700">12,400.00</td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-purple-900">12,400.00</td>
                <td className="px-4 py-2 text-center text-indigo-600 font-sans"><ChevronRight className="w-4 h-4 mx-auto" /></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Invoice-Level Drill-Down Modal */}
      {drillDownModal && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-xl shadow-2xl border border-slate-200 max-w-4xl w-full p-6 space-y-4 max-h-[85vh] flex flex-col">
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
              <div>
                <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 bg-indigo-50 text-indigo-700 rounded border border-indigo-200">
                  {drillDownModal.sectionCode}
                </span>
                <h3 className="text-base font-bold text-slate-900 mt-1">
                  {drillDownModal.title}
                </h3>
              </div>
              <button
                onClick={() => setDrillDownModal(null)}
                className="p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="overflow-y-auto flex-1 border border-slate-200 rounded-lg">
              <table className="w-full text-left text-xs font-mono">
                <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 font-sans font-semibold sticky top-0">
                  <tr>
                    <th className="px-3 py-2.5">Document No & Date</th>
                    <th className="px-3 py-2.5">Supplier GSTIN & Legal Name</th>
                    <th className="px-3 py-2.5 text-right">Taxable (₹)</th>
                    <th className="px-3 py-2.5 text-right">CGST (₹)</th>
                    <th className="px-3 py-2.5 text-right">SGST (₹)</th>
                    <th className="px-3 py-2.5 text-right">IGST (₹)</th>
                    <th className="px-3 py-2.5 text-right font-bold">Total (₹)</th>
                    <th className="px-3 py-2.5">Statutory Provenance</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {drillDownModal.items.map((it, idx) => (
                    <tr key={idx} className="hover:bg-slate-50">
                      <td className="px-3 py-2.5">
                        <div className="font-bold text-slate-900">{it.docNo}</div>
                        <div className="text-[10px] text-slate-500 font-sans">{it.docDate}</div>
                      </td>
                      <td className="px-3 py-2.5 font-sans">
                        <div className="font-medium text-slate-900">{it.supplierName}</div>
                        <div className="text-[10px] text-slate-500 font-mono">{it.supplierGstin}</div>
                      </td>
                      <td className="px-3 py-2.5 text-right">₹{it.taxable}</td>
                      <td className="px-3 py-2.5 text-right">₹{it.cgst}</td>
                      <td className="px-3 py-2.5 text-right">₹{it.sgst}</td>
                      <td className="px-3 py-2.5 text-right">₹{it.igst}</td>
                      <td className="px-3 py-2.5 text-right font-bold text-slate-900">₹{it.total}</td>
                      <td className="px-3 py-2.5 font-sans">
                        <div className="font-semibold text-indigo-700 text-[10px]">{it.ruleCode || "N/A"}</div>
                        {it.priorPeriod && (
                          <div className="text-[10px] text-slate-500">Prior 3B: {it.priorPeriod}</div>
                        )}
                        {it.interestBasis && (
                          <div className="text-[9px] text-rose-600 mt-0.5">{it.interestBasis}</div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex items-center justify-between pt-2 border-t border-slate-100 text-xs">
              <span className="text-slate-500">
                Total Documents in Section: <strong>{drillDownModal.items.length}</strong>
              </span>
              <button
                onClick={() => setDrillDownModal(null)}
                className="px-4 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg font-medium transition-colors"
              >
                Close Audit View
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
