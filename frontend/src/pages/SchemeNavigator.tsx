import React, { useState, useEffect } from "react";
import { 
  Award, 
  CheckCircle2, 
  XCircle, 
  ExternalLink, 
  FileText, 
  Building2, 
  RefreshCw, 
  Filter, 
  ChevronRight,
  ShieldCheck,
  AlertCircle
} from "lucide-react";
import { 
  listGovernmentSchemes, 
  getBusinessProfile, 
  updateBusinessProfile, 
  evaluateSchemes, 
  getSchemeChecklist,
  GovernmentSchemeOut,
  BusinessProfileOut,
  SchemeMatchOut,
  SchemeChecklistOut
} from "../api";

export function SchemeNavigator() {
  const [schemes, setSchemes] = useState<GovernmentSchemeOut[]>([]);
  const [matches, setMatches] = useState<SchemeMatchOut[]>([]);
  const [profile, setProfile] = useState<BusinessProfileOut | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string>("");
  const [activeChecklist, setActiveChecklist] = useState<SchemeChecklistOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [isEditingProfile, setIsEditingProfile] = useState(false);
  const [notification, setNotification] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Edit profile state
  const [editSector, setEditSector] = useState("Manufacturing");
  const [editType, setEditType] = useState("Proprietorship");
  const [editTurnover, setEditTurnover] = useState("2500000");
  const [editUdyam, setEditUdyam] = useState("UDYAM-MH-12-0012345");

  const loadAll = async () => {
    setLoading(true);
    setError(null);
    try {
      const [schList, prof] = await Promise.all([
        listGovernmentSchemes(selectedCategory || undefined),
        getBusinessProfile()
      ]);
      setSchemes(schList);
      setProfile(prof);
      if (prof) {
        setEditSector(prof.industry_sector);
        setEditType(prof.business_type);
        setEditTurnover(prof.annual_turnover ? String(prof.annual_turnover) : "2500000");
        setEditUdyam(prof.udyam_number || "");
      }
      // Also evaluate
      const matchResults = await evaluateSchemes();
      setMatches(matchResults);
    } catch (err: any) {
      setError(err?.message || "Failed to load scheme navigator data.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, [selectedCategory]);

  const handleEvaluate = async () => {
    setEvaluating(true);
    setError(null);
    try {
      const matchResults = await evaluateSchemes();
      setMatches(matchResults);
      setNotification(`Evaluated ${matchResults.length} schemes against your business profile.`);
      setTimeout(() => setNotification(null), 4000);
    } catch (err: any) {
      setError(err?.message || "Evaluation failed.");
    } finally {
      setEvaluating(false);
    }
  };

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const updated = await updateBusinessProfile({
        business_type: editType,
        industry_sector: editSector,
        turnover_bracket: "Micro (< 5 Cr)",
        annual_turnover: parseFloat(editTurnover) || 0,
        udyam_number: editUdyam || null,
        vintage_years: 3,
        social_category: "General",
        has_udyam: Boolean(editUdyam)
      });
      setProfile(updated);
      setIsEditingProfile(false);
      setNotification("Business profile updated. Re-evaluating eligibility...");
      const matchResults = await evaluateSchemes();
      setMatches(matchResults);
      setTimeout(() => setNotification(null), 4000);
    } catch (err: any) {
      setError(err?.message || "Profile update failed.");
    } finally {
      setLoading(false);
    }
  };

  const handleOpenChecklist = async (schemeCode: string) => {
    try {
      const chk = await getSchemeChecklist(schemeCode);
      setActiveChecklist(chk);
    } catch (err: any) {
      setError(err?.message || "Could not retrieve scheme checklist.");
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-xl md:text-2xl font-bold text-slate-800 flex items-center gap-2">
            <Award className="w-6 h-6 text-indigo-600" />
            Government Scheme Navigator
          </h2>
          <p className="text-sm text-slate-500">
            Deterministic Eligibility Matcher for Indian MSME Subsidies, Grants & Guarantee Schemes
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsEditingProfile(!isEditingProfile)}
            className="px-3 py-1.5 bg-white border border-slate-300 text-slate-700 hover:bg-slate-50 rounded-lg text-xs font-semibold shadow-sm"
          >
            {isEditingProfile ? "Close Profile Editor" : "Edit MSME Profile"}
          </button>
          <button
            onClick={handleEvaluate}
            disabled={evaluating}
            className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 transition shadow-sm"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${evaluating ? "animate-spin" : ""}`} />
            {evaluating ? "Evaluating..." : "Run Match Engine"}
          </button>
        </div>
      </div>

      {/* Notifications */}
      {notification && (
        <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-xl text-emerald-800 text-sm flex items-center gap-2">
          <CheckCircle2 className="w-5 h-5 flex-shrink-0 text-emerald-600" />
          <span>{notification}</span>
        </div>
      )}

      {error && (
        <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-rose-800 text-sm flex items-center gap-2">
          <AlertCircle className="w-5 h-5 flex-shrink-0 text-rose-600" />
          <span>{error}</span>
        </div>
      )}

      {/* Profile Overview Bar / Editor */}
      <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        {isEditingProfile ? (
          <form onSubmit={handleSaveProfile} className="space-y-4">
            <div className="text-sm font-bold text-slate-800 border-b border-slate-100 pb-2">
              Update MSME Eligibility Parameters
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
              <label className="text-xs font-semibold text-slate-600">
                Industry Sector
                <select
                  value={editSector}
                  onChange={(e) => setEditSector(e.target.value)}
                  className="mt-1 w-full p-2 text-xs border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
                >
                  <option value="Manufacturing">Manufacturing</option>
                  <option value="Services">Services</option>
                  <option value="Retail">Retail</option>
                  <option value="Trading">Trading</option>
                </select>
              </label>

              <label className="text-xs font-semibold text-slate-600">
                Business Constitution
                <select
                  value={editType}
                  onChange={(e) => setEditType(e.target.value)}
                  className="mt-1 w-full p-2 text-xs border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
                >
                  <option value="Proprietorship">Proprietorship</option>
                  <option value="Partnership">Partnership</option>
                  <option value="Private Limited">Private Limited</option>
                  <option value="LLP">LLP</option>
                  <option value="Self Help Group">Self Help Group</option>
                </select>
              </label>

              <label className="text-xs font-semibold text-slate-600">
                Annual Turnover (₹)
                <input
                  type="number"
                  value={editTurnover}
                  onChange={(e) => setEditTurnover(e.target.value)}
                  className="mt-1 w-full p-2 text-xs border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
                  placeholder="e.g. 2500000"
                />
              </label>

              <label className="text-xs font-semibold text-slate-600">
                Udyam Registration No.
                <input
                  type="text"
                  value={editUdyam}
                  onChange={(e) => setEditUdyam(e.target.value)}
                  className="mt-1 w-full p-2 text-xs border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
                  placeholder="UDYAM-MH-12-0012345"
                />
              </label>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setIsEditingProfile(false)}
                className="px-3 py-1.5 text-xs text-slate-600 font-semibold"
              >
                Cancel
              </button>
              <button
                type="submit"
                className="px-4 py-1.5 bg-indigo-600 text-white rounded-lg text-xs font-semibold shadow-sm"
              >
                Save & Recalculate Matches
              </button>
            </div>
          </form>
        ) : (
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div className="flex flex-wrap items-center gap-6 text-xs">
              <div>
                <span className="text-slate-400 font-medium">Sector:</span>{" "}
                <strong className="text-slate-800">{profile?.industry_sector || "Manufacturing"}</strong>
              </div>
              <div>
                <span className="text-slate-400 font-medium">Structure:</span>{" "}
                <strong className="text-slate-800">{profile?.business_type || "Proprietorship"}</strong>
              </div>
              <div>
                <span className="text-slate-400 font-medium">Turnover:</span>{" "}
                <strong className="text-slate-800">₹{profile?.annual_turnover || "25,00,000"}</strong>
              </div>
              <div>
                <span className="text-slate-400 font-medium">Udyam No:</span>{" "}
                <strong className="text-indigo-600 font-mono">{profile?.udyam_number || "Not Registered"}</strong>
              </div>
            </div>
            <div className="flex items-center gap-1.5 text-xs text-emerald-700 bg-emerald-50 px-2.5 py-1 rounded-full font-semibold">
              <ShieldCheck className="w-3.5 h-3.5" />
              Verified MSME Profile
            </div>
          </div>
        )}
      </div>

      {/* Category Filter Pills */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1">
        <Filter className="w-4 h-4 text-slate-400 mr-1" />
        {[
          { label: "All Categories", value: "" },
          { label: "Credit Guarantee", value: "credit_guarantee" },
          { label: "Government Subsidy", value: "subsidy" },
          { label: "Tech Upgradation", value: "technology_upgradation" },
          { label: "Skill Development", value: "skill_development" }
        ].map((cat) => (
          <button
            key={cat.value}
            onClick={() => setSelectedCategory(cat.value)}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition-colors ${
              selectedCategory === cat.value
                ? "bg-indigo-600 text-white shadow-sm"
                : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-50"
            }`}
          >
            {cat.label}
          </button>
        ))}
      </div>

      {/* Matched Schemes List */}
      <div className="space-y-4">
        {matches.map((m) => {
          const scoreNum = typeof m.match_score === "string" ? parseFloat(m.match_score) : m.match_score;
          return (
            <div 
              key={m.scheme_id} 
              className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm hover:border-indigo-200 transition"
            >
              <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-bold text-indigo-600 font-mono bg-indigo-50 px-2 py-0.5 rounded">
                      {m.scheme_code}
                    </span>
                    <span className="text-xs text-slate-400 font-medium">
                      {m.ministry}
                    </span>
                  </div>
                  <h3 className="text-base font-bold text-slate-900">{m.scheme_name}</h3>
                  <p className="text-xs text-slate-600 mt-1 max-w-2xl">{m.benefits_summary}</p>
                </div>

                <div className="flex items-center gap-4 self-end sm:self-auto">
                  <div className="text-right">
                    <div className="text-[11px] text-slate-400 uppercase font-semibold">Match Score</div>
                    <div className={`text-2xl font-black ${
                      scoreNum >= 80 ? "text-emerald-600" :
                      scoreNum >= 50 ? "text-indigo-600" :
                      "text-slate-400"
                    }`}>
                      {scoreNum}%
                    </div>
                  </div>
                  <button
                    onClick={() => handleOpenChecklist(m.scheme_code)}
                    className="px-3 py-2 bg-slate-900 hover:bg-slate-800 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 transition shadow-sm"
                  >
                    <span>Checklist & Apply</span>
                    <ChevronRight className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              {/* Criteria Tags */}
              <div className="mt-4 pt-3 border-t border-slate-100 flex flex-wrap gap-2">
                {m.evaluated_criteria?.map((c, idx) => (
                  <div 
                    key={idx}
                    className={`text-[11px] px-2.5 py-1 rounded-md font-medium flex items-center gap-1.5 ${
                      c.passed 
                        ? "bg-emerald-50 text-emerald-800 border border-emerald-100" 
                        : "bg-rose-50 text-rose-700 border border-rose-100"
                    }`}
                  >
                    {c.passed ? (
                      <CheckCircle2 className="w-3 h-3 text-emerald-600" />
                    ) : (
                      <XCircle className="w-3 h-3 text-rose-500" />
                    )}
                    <span>{c.description}</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      {/* Application Checklist Modal */}
      {activeChecklist && (
        <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl border border-slate-200 max-w-2xl w-full p-6 shadow-xl max-h-[90vh] overflow-y-auto space-y-5">
            <div className="flex justify-between items-start">
              <div>
                <span className="text-xs font-bold text-indigo-600 uppercase tracking-wider">{activeChecklist.scheme_code}</span>
                <h3 className="text-lg font-bold text-slate-900 mt-1">{activeChecklist.name}</h3>
                <p className="text-xs text-slate-500">{activeChecklist.ministry}</p>
              </div>
              <button 
                onClick={() => setActiveChecklist(null)}
                className="text-slate-400 hover:text-slate-600 text-lg font-bold p-1"
              >
                &times;
              </button>
            </div>

            <div className="p-3 bg-indigo-50 border border-indigo-100 rounded-xl text-xs text-indigo-800">
              <strong>Eligibility Criteria:</strong> {activeChecklist.eligibility_summary}
            </div>

            {/* Required Documents */}
            <div>
              <h4 className="text-xs font-bold text-slate-800 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                <FileText className="w-4 h-4 text-slate-600" />
                Required Documentation Checklist
              </h4>
              <ul className="space-y-2">
                {activeChecklist.required_documents.map((doc, idx) => (
                  <li key={idx} className="p-2.5 bg-slate-50 rounded-lg border border-slate-100 text-xs text-slate-700 flex items-center gap-2">
                    <CheckCircle2 className="w-3.5 h-3.5 text-indigo-600 flex-shrink-0" />
                    <span>{doc}</span>
                  </li>
                ))}
              </ul>
            </div>

            {/* Application Steps */}
            <div>
              <h4 className="text-xs font-bold text-slate-800 uppercase tracking-wider mb-2">
                Official Application Workflow
              </h4>
              <ol className="space-y-2">
                {activeChecklist.application_steps.map((step, idx) => (
                  <li key={idx} className="text-xs text-slate-600 pl-2">
                    {step}
                  </li>
                ))}
              </ol>
            </div>

            {/* Modal Actions */}
            <div className="pt-4 border-t border-slate-100 flex justify-between items-center">
              <a
                href={activeChecklist.portal_url}
                target="_blank"
                rel="noreferrer"
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 shadow-sm transition"
              >
                <span>Access Government Portal</span>
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
              <button
                onClick={() => setActiveChecklist(null)}
                className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-semibold"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
