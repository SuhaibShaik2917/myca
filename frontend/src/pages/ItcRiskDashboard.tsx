import React, { useState } from "react";
import { AlertOctagon, CheckCircle2, Clock, ShieldCheck, FileText, DollarSign, Calendar, Info } from "lucide-react";

export const ItcRiskDashboard: React.FC = () => {
  const [returnPeriod, setReturnPeriod] = useState("2026-11");

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
      status: "REVERSAL_TRIGGERED"
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
      status: "35_DAYS_REMAINING"
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
      actionNeeded: "Reverse in Nov 2026 GSTR-3B under Table 4(B)(2)"
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

  return (
    <div className="space-y-6">
      {/* 4 Distinct Non-Confused Subtotals Cards */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-bold text-slate-800 uppercase tracking-wider flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-emerald-600" />
            Statutory ITC Tally & Distinct Non-Confused Subtotals ({returnPeriod})
          </h2>
          <span className="text-xs text-slate-500 font-mono">Rule Engine v2.1</span>
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

      {/* Rule 37: 180-Day Vendor Payment Tracking */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 space-y-4">
        <div className="flex items-center justify-between pb-3 border-b border-slate-100">
          <div>
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <Clock className="w-4 h-4 text-amber-600" />
              Rule 37: 180-Day Vendor Payment Aging & Reversal Engine
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Statutory reversal on unpaid vendor invoices after 180 days with mandatory 18% p.a. interest exposure. Re-availment permissible upon subsequent payment.
            </p>
          </div>
          <span className="px-2 py-0.5 bg-amber-50 text-amber-700 border border-amber-200 rounded text-xs font-semibold">
            Section 16(2) Proviso
          </span>
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
                <th className="px-3 py-2 text-right">18% Interest Liability</th>
                <th className="px-3 py-2 text-center">Statutory Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 font-mono">
              {rule37AgingRows.map((r, i) => (
                <tr key={i} className="hover:bg-slate-50">
                  <td className="px-3 py-2.5">
                    <div className="font-bold text-slate-900">{r.docNo}</div>
                    <div className="text-[11px] text-slate-500 font-sans">{r.supplier}</div>
                  </td>
                  <td className="px-3 py-2.5 font-sans">{r.invoiceDate}</td>
                  <td className="px-3 py-2.5 text-center font-bold text-slate-800">{r.daysElapsed} days</td>
                  <td className="px-3 py-2.5 text-right font-medium">₹{r.invoiceValue}</td>
                  <td className="px-3 py-2.5 text-right font-bold text-rose-600">₹{r.unpaidAmount}</td>
                  <td className="px-3 py-2.5 text-right font-bold text-amber-600">₹{r.reversalRequired}</td>
                  <td className="px-3 py-2.5 text-right font-bold text-rose-700">₹{r.interestExposure}</td>
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
            Supplier reported in GSTR-1 but failed to furnish Form GSTR-3B by 30th September. ITC must be reversed by 30th November. If reversed after 30th November, 18% p.a. interest applies under Section 50.
          </p>
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
            <span className="text-[11px] text-purple-700 font-semibold font-mono">Catalogue Filtered</span>
          </div>
          <p className="text-[11px] text-slate-500">
            Statutorily blocked items (motor vehicles, food/beverages, works contract) separated from eligible ITC.
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

      {/* GSTR-3B Table 4 Working Summary Output */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 space-y-4">
        <div className="flex items-center justify-between pb-3 border-b border-slate-100">
          <div>
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <FileText className="w-4 h-4 text-indigo-600" />
              Form GSTR-3B Table 4 Inward ITC Working Summary
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Reconciled working summary distinguishing portal communicated amounts, book figures, and net statutory claim.
            </p>
          </div>
          <span className="text-xs text-amber-700 bg-amber-50 border border-amber-200 px-2.5 py-1 rounded-full font-medium">
            Draft Working Output (Not official tax advice)
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
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {/* 4(A) ITC Available */}
              <tr className="bg-emerald-50/40 font-sans font-bold text-slate-800">
                <td colSpan={6} className="px-4 py-2 text-[11px] text-emerald-800">
                  (A) ITC Available (whether in full or in part)
                </td>
              </tr>
              <tr>
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">(1) Import of Goods (ICEGATE)</td>
                <td className="px-4 py-2 text-right">60,000.00</td>
                <td className="px-4 py-2 text-right">0.00</td>
                <td className="px-4 py-2 text-right">0.00</td>
                <td className="px-4 py-2 text-right">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-slate-900">60,000.00</td>
              </tr>
              <tr>
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">(3) Inward Supplies Attracting Reverse Charge (RCM)</td>
                <td className="px-4 py-2 text-right">10,000.00</td>
                <td className="px-4 py-2 text-right">10,000.00</td>
                <td className="px-4 py-2 text-right">10,000.00</td>
                <td className="px-4 py-2 text-right">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-slate-900">30,000.00</td>
              </tr>
              <tr>
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">(4) Inward Supplies from ISD</td>
                <td className="px-4 py-2 text-right">15,000.00</td>
                <td className="px-4 py-2 text-right">5,250.00</td>
                <td className="px-4 py-2 text-right">5,250.00</td>
                <td className="px-4 py-2 text-right">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-slate-900">25,500.00</td>
              </tr>
              <tr>
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">(5) All Other ITC (Registered Regular B2B)</td>
                <td className="px-4 py-2 text-right">1,20,000.00</td>
                <td className="px-4 py-2 text-right">1,23,500.00</td>
                <td className="px-4 py-2 text-right">1,23,500.00</td>
                <td className="px-4 py-2 text-right">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-slate-900">3,67,000.00</td>
              </tr>

              {/* 4(B) ITC Reversed */}
              <tr className="bg-amber-50/40 font-sans font-bold text-slate-800">
                <td colSpan={6} className="px-4 py-2 text-[11px] text-amber-800">
                  (B) ITC Reversed
                </td>
              </tr>
              <tr>
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">(1) As per rules 38, 42 and 43 of CGST Rules and section 17(5) (Non-Reclaimable Reversals)</td>
                <td className="px-4 py-2 text-right text-rose-600">65,000.00</td>
                <td className="px-4 py-2 text-right text-rose-600">6,250.00</td>
                <td className="px-4 py-2 text-right text-rose-600">6,250.00</td>
                <td className="px-4 py-2 text-right text-rose-600">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-rose-700">77,500.00</td>
              </tr>
              <tr>
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">(2) Others: Rule 37 (180-Day Non-Payment ₹18,000) + Rule 37A (Supplier Non-Filing ₹32,400) (Reclaimable)</td>
                <td className="px-4 py-2 text-right text-rose-600">32,400.00</td>
                <td className="px-4 py-2 text-right text-rose-600">9,000.00</td>
                <td className="px-4 py-2 text-right text-rose-600">9,000.00</td>
                <td className="px-4 py-2 text-right text-rose-600">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-rose-700">50,400.00</td>
              </tr>

              {/* 4(C) Net ITC */}
              <tr className="bg-indigo-50 font-sans font-bold text-slate-900">
                <td className="px-4 py-2.5">(C) Net ITC Available (A) - (B) [4(A) ₹4,82,500 - 4(B)(1) ₹77,500 - 4(B)(2) ₹50,400]</td>
                <td className="px-4 py-2.5 text-right font-mono">1,07,600.00</td>
                <td className="px-4 py-2.5 text-right font-mono">1,23,500.00</td>
                <td className="px-4 py-2.5 text-right font-mono">1,23,500.00</td>
                <td className="px-4 py-2.5 text-right font-mono">0.00</td>
                <td className="px-4 py-2.5 text-right font-mono text-indigo-700 text-sm font-extrabold">3,54,600.00</td>
              </tr>

              {/* 4(D) Other Details */}
              <tr className="bg-purple-50/40 font-sans font-bold text-slate-800">
                <td colSpan={6} className="px-4 py-2 text-[11px] text-purple-800">
                  (D) Other Details
                </td>
              </tr>
              <tr>
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">(1) ITC reclaimed which was reversed under Table 4(B)(2) in earlier tax period (Never Section 17(5))</td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-purple-900">0.00</td>
              </tr>
              <tr>
                <td className="px-4 py-2 pl-8 font-sans text-slate-700">(2) Ineligible ITC under section 16(4) & PoS restrictions (from GSTR-2B Table 4 unavailable; never added to 4(A))</td>
                <td className="px-4 py-2 text-right text-purple-700">12,400.00</td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right text-purple-700">0.00</td>
                <td className="px-4 py-2 text-right font-bold text-purple-900">12,400.00</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
