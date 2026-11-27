import React, { useState, useEffect } from "react";
import {
  Compass,
  CheckCircle2,
  XCircle,
  ExternalLink,
  FileText,
  Sparkles,
  RefreshCw,
  Building2,
  BadgePercent,
  AlertCircle,
} from "lucide-react";
import {
  getBusinessProfile,
  updateBusinessProfile,
  evaluateSchemes,
  getSchemeChecklist,
  BusinessProfile,
  SchemeMatch,
  SchemeChecklist,
} from "../api";

export default function SchemeNavigator() {
  const [profile, setProfile] = useState<BusinessProfile | null>(null);
  const [matches, setMatches] = useState<SchemeMatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [activeChecklist, setActiveChecklist] = useState<SchemeChecklist | null>(null);
  const [editMode, setEditMode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notification, setNotification] = useState<string | null>(null);

  // Form states for profile update (all persisted fields bound to UI inputs)
  const [bType, setBType] = useState("Proprietorship");
  const [sector, setSector] = useState("Manufacturing");
  const [bracket, setBracket] = useState("Micro (< 5 Cr)");
  const [turnover, setTurnover] = useState("0");
  const [udyam, setUdyam] = useState("");
  const [vintageYears, setVintageYears] = useState("");
  const [socialCategory, setSocialCategory] = useState("General");
  const [hasUdyam, setHasUdyam] = useState(false);

  const syncForm = (prof: BusinessProfile) => {
    setBType(prof.business_type || "Proprietorship");
    setSector(prof.industry_sector || "Manufacturing");
    setBracket(prof.turnover_bracket || "Micro (< 5 Cr)");
    setTurnover(String(prof.annual_turnover ?? "0"));
    setUdyam(prof.udyam_number || "");
    setVintageYears(
      prof.vintage_years !== null && prof.vintage_years !== undefined
        ? String(prof.vintage_years)
        : ""
    );
    setSocialCategory(prof.social_category || "General");
    setHasUdyam(Boolean(prof.has_udyam ?? prof.udyam_number));
  };

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [prof, evalResults] = await Promise.all([
        getBusinessProfile(),
        evaluateSchemes(),
      ]);
      setProfile(prof);
      syncForm(prof);
      setMatches(evalResults);
    } catch (err: any) {
      setError(err.message || "Failed to load MSME scheme matching profile.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    setEvaluating(true);
    setError(null);
    try {
      const parsedVintage = vintageYears.trim() === "" ? null : Number(vintageYears);
      const updated = await updateBusinessProfile({
        business_type: bType,
        industry_sector: sector,
        turnover_bracket: bracket,
        annual_turnover: parseFloat(turnover) || 0,
        udyam_number: udyam.trim() || null,
        vintage_years: parsedVintage !== null && Number.isFinite(parsedVintage) ? parsedVintage : null,
        social_category: socialCategory,
        has_udyam: Boolean(udyam.trim()) || hasUdyam,
      });
      setProfile(updated);
      syncForm(updated);
      const evalResults = await evaluateSchemes();
      setMatches(evalResults);
      setEditMode(false);
      setNotification("Enterprise profile updated and statutory eligibility recalculated.");
    } catch (err: any) {
      setError(err.message || "Profile update failed.");
    } finally {
      setEvaluating(false);
    }
  };

  const handleOpenChecklist = async (schemeCode: string) => {
    setError(null);
    try {
      const chk = await getSchemeChecklist(schemeCode);
      setActiveChecklist(chk);
    } catch (err: any) {
      setError(err.message || "Failed to retrieve official scheme checklist.");
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <Compass className="w-5 h-5 text-indigo-600" />
            MSME Government Scheme Navigator
          </h2>
          <p className="text-sm text-slate-500 mt-1">
            Automated deterministic eligibility evaluation across Central Ministry of MSME, SIDBI, and Credit Guarantee schemes.
          </p>
        </div>

        <button
          onClick={() => setEditMode(!editMode)}
          className="px-4 py-2 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition"
        >
          <Building2 className="w-3.5 h-3.5" />
          <span>{editMode ? "Cancel Edit" : "Edit Enterprise Profile"}</span>
        </button>
      </div>

      {error && (
        <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-xs text-rose-700 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      {notification && (
        <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-xl text-xs text-emerald-700">
          {notification}
        </div>
      )}

      {/* Profile Summary / Edit Card */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
        {editMode ? (
          <form onSubmit={handleSaveProfile} className="space-y-4">
            <h3 className="text-sm font-bold text-slate-800 border-b border-slate-100 pb-2">
              Update Enterprise Statutory Attributes
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <div>
                <label className="block text-xs font-semibold text-slate-600 mb-1">Entity Constitution</label>
                <select
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs"
                  value={bType}
                  onChange={(e) => setBType(e.target.value)}
                >
                  <option value="Proprietorship">Proprietorship</option>
                  <option value="Partnership">Partnership</option>
                  <option value="Private Limited">Private Limited / LLP</option>
                  <option value="Individual">Individual Artisan</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-600 mb-1">Industry Sector</label>
                <select
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs"
                  value={sector}
                  onChange={(e) => setSector(e.target.value)}
                >
                  <option value="Manufacturing">Manufacturing</option>
                  <option value="Services">Services &amp; Consulting</option>
                  <option value="Trading">Wholesale / Trading</option>
                  <option value="Retail">Retail / Street Vendor</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-600 mb-1">MSME Category Bracket</label>
                <select
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs"
                  value={bracket}
                  onChange={(e) => setBracket(e.target.value)}
                >
                  <option value="Micro (< 5 Cr)">Micro (&lt; ₹5 Cr)</option>
                  <option value="Small (5-50 Cr)">Small (₹5–50 Cr)</option>
                  <option value="Medium (50-250 Cr)">Medium (₹50–250 Cr)</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-600 mb-1">Annual Turnover (INR)</label>
                <input
                  type="number"
                  step="0.01"
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs"
                  value={turnover}
                  onChange={(e) => setTurnover(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-600 mb-1">Udyam Registration Number</label>
                <input
                  type="text"
                  placeholder="UDYAM-MH-01-0001234"
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs font-mono"
                  value={udyam}
                  onChange={(e) => {
                    const v = e.target.value.toUpperCase();
                    setUdyam(v);
                    if (v.trim()) setHasUdyam(true);
                  }}
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-600 mb-1">Business Vintage (Years)</label>
                <input
                  type="number"
                  min={0}
                  max={100}
                  placeholder="e.g. 2"
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs font-mono"
                  value={vintageYears}
                  onChange={(e) => setVintageYears(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-600 mb-1">Promoter Category</label>
                <select
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs"
                  value={socialCategory}
                  onChange={(e) => setSocialCategory(e.target.value)}
                >
                  <option value="General">General</option>
                  <option value="OBC">OBC</option>
                  <option value="SC">SC</option>
                  <option value="ST">ST</option>
                  <option value="Women">Women Entrepreneur</option>
                  <option value="Minority">Minority</option>
                </select>
              </div>
              <div className="flex items-end pb-2">
                <label className="flex items-center gap-2 text-xs font-semibold text-slate-700 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={hasUdyam}
                    onChange={(e) => setHasUdyam(e.target.checked)}
                    className="rounded border-slate-300 text-indigo-600"
                  />
                  <span>Active Udyam Registration</span>
                </label>
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <button
                type="submit"
                disabled={evaluating}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold flex items-center gap-2"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${evaluating ? "animate-spin" : ""}`} />
                <span>{evaluating ? "Evaluating Rules..." : "Save & Recalculate Eligibility"}</span>
              </button>
            </div>
          </form>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex flex-wrap gap-6 text-xs">
              <div>
                <span className="text-slate-400 block mb-0.5">Constitution</span>
                <span className="font-bold text-slate-800">{profile?.business_type || "Not Configured"}</span>
              </div>
              <div>
                <span className="text-slate-400 block mb-0.5">Industry Sector</span>
                <span className="font-bold text-slate-800">{profile?.industry_sector || "Not Configured"}</span>
              </div>
              <div>
                <span className="text-slate-400 block mb-0.5">Turnover Scale</span>
                <span className="font-bold text-slate-800">
                  {profile?.turnover_bracket || "Micro (< 5 Cr)"} (
                  {profile?.annual_turnover !== undefined && profile?.annual_turnover !== null
                    ? `₹${Number(profile.annual_turnover).toLocaleString("en-IN")}`
                    : "Not Set"}
                  )
                </span>
              </div>
              <div>
                <span className="text-slate-400 block mb-0.5">Vintage &amp; Category</span>
                <span className="font-bold text-slate-800">
                  {profile?.vintage_years !== null && profile?.vintage_years !== undefined
                    ? `${profile.vintage_years} yrs`
                    : "Vintage unset"}{" "}
                  • {profile?.social_category || "General"}
                </span>
              </div>
              <div>
                <span className="text-slate-400 block mb-0.5">Udyam Registration</span>
                <span className="font-mono font-bold text-indigo-700">
                  {profile?.udyam_number || (profile?.has_udyam ? "Registered" : "Not Registered")}
                </span>
              </div>
            </div>

            <div className="flex items-center gap-2 text-xs bg-emerald-50 text-emerald-700 px-3 py-1.5 rounded-full font-semibold">
              <Sparkles className="w-3.5 h-3.5" />
              <span>{matches.filter((m) => Number(m.match_score) >= 75).length} Priority Matches Found</span>
            </div>
          </div>
        )}
      </div>

      {/* Matches Grid */}
      <div className="grid grid-cols-1 gap-4">
        {loading && matches.length === 0 ? (
          <div className="p-8 text-center text-xs text-slate-500 bg-white rounded-xl border border-slate-200">
            Evaluating statutory eligibility criteria across Central MSME schemes...
          </div>
        ) : matches.length === 0 ? (
          <div className="p-8 text-center text-xs text-slate-500 bg-white rounded-xl border border-slate-200">
            No scheme matches evaluated yet. Authenticate and save your enterprise profile to evaluate eligibility.
          </div>
        ) : null}
        {matches.map((m) => {
          const scoreNum = Number(m.match_score);
          const isMatch = scoreNum >= 75;
          return (
            <div
              key={m.scheme_id}
              className={`bg-white rounded-xl border p-5 shadow-sm transition hover:shadow-md ${
                isMatch ? "border-emerald-200" : "border-slate-200"
              }`}
            >
              <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-bold text-indigo-600 font-mono bg-indigo-50 px-2 py-0.5 rounded">
                      {m.scheme_code}
                    </span>
                    <span className="text-xs text-slate-400 font-medium">{m.ministry}</span>
                    {m.max_subsidy_amount && (
                      <span className="text-[11px] text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded font-semibold flex items-center gap-1">
                        <BadgePercent className="w-3 h-3" />
                        Up to {m.max_subsidy_amount}
                      </span>
                    )}
                  </div>
                  <h3 className="text-base font-bold text-slate-900">{m.scheme_name}</h3>
                  <p className="text-xs text-slate-600 mt-1 max-w-2xl">{m.benefits_summary}</p>
                </div>

                <div className="flex items-center gap-4 self-end sm:self-auto">
                  <div className="text-right">
                    <div className="text-[11px] text-slate-400 uppercase font-semibold">Match Score</div>
                    <div
                      className={`text-2xl font-black ${
                        scoreNum >= 80
                          ? "text-emerald-600"
                          : scoreNum >= 50
                          ? "text-indigo-600"
                          : "text-slate-400"
                      }`}
                    >
                      {scoreNum}%
                    </div>
                  </div>
                  <button
                    onClick={() => handleOpenChecklist(m.scheme_code)}
                    className="px-3 py-2 bg-slate-900 hover:bg-slate-800 text-white rounded-lg text-xs font-semibold transition shadow-sm"
                  >
                    Checklist &amp; Apply
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
                <span className="text-xs font-bold text-indigo-600 uppercase tracking-wider">
                  {activeChecklist.scheme_code}
                </span>
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

            <div>
              <h4 className="text-xs font-bold text-slate-800 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                <FileText className="w-4 h-4 text-slate-600" />
                Required Documentation Checklist
              </h4>
              <ul className="space-y-2">
                {activeChecklist.required_documents.map((doc, idx) => (
                  <li
                    key={idx}
                    className="p-2.5 bg-slate-50 rounded-lg border border-slate-100 text-xs text-slate-700 flex items-center gap-2"
                  >
                    <CheckCircle2 className="w-3.5 h-3.5 text-indigo-600 flex-shrink-0" />
                    <span>{doc}</span>
                  </li>
                ))}
              </ul>
            </div>

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
