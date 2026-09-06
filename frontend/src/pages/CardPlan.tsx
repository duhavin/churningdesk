import { Fragment, useEffect, useMemo, useState } from "react";
import { api, type CardReference, type CatalogEntry } from "../lib/api";
import type { Flash } from "../App";
import {
  Banner,
  Card,
  EmptyState,
  Field,
  Modal,
  RareBadge,
  Spinner,
  StatusBadge,
  cardName,
  decisionCondition,
  decisionDisplayStatus,
  fmtMoney,
  fmtNum,
} from "../components/ui";

const STATUS_ORDER = ["APPLY NOW", "WATCH", "WAIT", "NEEDS DATA", "LOW PRIORITY", "FUTURE", "SKIP"];
const ACTIONABLE_STATUSES = new Set(["APPLY NOW", "WATCH", "WAIT"]);

type OfferFilter = "known" | "all" | "needs_data" | "actionable" | "pipeline";
type SortMode = "ranked" | "value" | "peak_score" | "name";

function multiplierDisplay(value: any) {
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return `${numeric.toLocaleString(undefined, { maximumFractionDigits: 2 })}x`;
  const text = String(value ?? "").trim();
  if (!text) return "";
  if (/^\d+(?:\.\d+)?$/.test(text)) return `${text}x`;
  return text;
}

function entryLabel(key: string) {
  return key.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function compactEntries(obj: Record<string, any> | null | undefined) {
  if (!obj) return "";
  return Object.entries(obj)
    .slice(0, 3)
    .map(([k, v]) => `${entryLabel(k)} ${multiplierDisplay(v) || String(v)}`)
    .join(" | ");
}

function qualityIssueLabel(issue: string) {
  const labels: Record<string, string> = {
    pending_verified_update: "verified update pending",
    source_identity_conflict: "source conflicts with card",
    broad_source_not_product_truth: "broad source only",
    source_not_product_specific: "source not product-specific",
    missing_source: "missing source",
    never_verified: "never verified",
    stale_verified_data: "stale verified data",
    missing_current_offer: "missing current offer",
    missing_public_peak: "missing public peak",
    missing_annual_fee: "missing annual fee",
    missing_currency: "missing currency",
    missing_min_spend: "missing minimum spend",
    missing_spend_window: "missing spend window",
  };
  return labels[issue] ?? issue.replace(/_/g, " ");
}

function decisionDataMessage(card: CatalogEntry) {
  const labels = (card.data_quality_issues ?? []).slice(0, 4).map(qualityIssueLabel);
  const suffix = labels.length ? `: ${labels.join(", ")}.` : ".";
  return `Incomplete public decision data${suffix} Review or fill the listed fields before ranking.`;
}

function useSummary(r: CatalogEntry) {
  return compactEntries(r.earn_multipliers) || compactEntries(r.best_category_uses);
}

function cleanBenefitText(value: any) {
  return String(value || "")
    .replace(/\[(?:text|title|meta|json-ld|table)\]\s*/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function benefitText(item: any) {
  const rawName =
    typeof item === "string"
      ? item
      : (item?.name ?? item?.benefit ?? item?.title ?? item?.description ?? "Benefit");
  const rawValue = typeof item === "string" ? null : (item?.value ?? item?.amount ?? item?.annual_value);
  const frequency = typeof item === "string" ? "" : item?.frequency && item.frequency !== "unknown" ? item.frequency : "";
  let text = cleanBenefitText([rawValue, rawName].filter(Boolean).join(" "));
  if (!text) return "";

  const low = text.toLowerCase().replace(/[\u2018\u2019]/g, "'");
  const noise = [
    "schema.org",
    "aggregaterating",
    "pay over time",
    "payment plan",
    "at checkout",
    "terms apply",
    "to learn more",
    "please visit",
    "whether you'd use",
    "rates and fees",
    "terms and conditions",
    "while we don't cover all available",
    "editorial content is not influenced",
    "not influenced by nor subject to review",
    "credit card company, bank or partner",
    "not all offers",
    "privacy",
    "cookie",
  ];
  if (noise.some((token) => low.includes(token))) return "";

  const amount = text.match(/\$\s*([0-9][0-9,]*(?:\.\d+)?)/)?.[0]?.replace(/\s+/g, "");
  const labelFromTerm =
    low.includes("resy") ? "Resy credit" :
    low.includes("uber") ? "Uber Cash" :
    low.includes("dunkin") ? "Dunkin credit" :
    low.includes("doordash") || low.includes("dashpass") ? "DoorDash membership" :
    low.includes("capital one travel") ? "Capital One Travel credit" :
    low.includes("global entry") || low.includes("tsa precheck") ? "Global Entry/TSA credit" :
    low.includes("priority pass") ? "Priority Pass" :
    low.includes("lounge") ? "Lounge access" :
    low.includes("clear") ? "CLEAR credit" :
    low.includes("travel") && low.includes("credit") ? "Travel credit" :
    low.includes("hotel") && low.includes("credit") ? "Hotel credit" :
    low.includes("dining") && low.includes("credit") ? "Dining credit" :
    low.includes("statement credit") ? "Statement credit" :
    "";

  if (labelFromTerm) {
    const cadence = frequency ? ` (${frequency})` : "";
    return amount && !labelFromTerm.toLowerCase().includes("membership")
      ? `${amount} ${labelFromTerm}${cadence}`
      : `${labelFromTerm}${cadence}`;
  }

  text = text.replace(/\bEnrollment required\.?\s*/gi, "");
  text = text.split(/[.;|]/, 1)[0];
  text = text.replace(/\s+/g, " ").trim();
  if (text.length > 72) text = `${text.slice(0, 69).trim()}...`;
  return text;
}

function cleanList(items: any[] | null | undefined) {
  if (!items?.length) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of items) {
    const label = benefitText(item);
    const key = label.toLowerCase();
    if (!label || seen.has(key)) continue;
    seen.add(key);
    out.push(label);
  }
  return out;
}

function compactList(items: any[] | null | undefined) {
  return cleanList(items).slice(0, 3).join(", ");
}

function detailEntries(obj: Record<string, any> | null | undefined) {
  if (!obj || Object.keys(obj).length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {Object.entries(obj).map(([k, v]) => (
        <span key={k} className="rounded border border-slate-800 bg-slate-950/30 px-2 py-0.5 text-[11px] text-slate-300">
          <span className="text-slate-500">{entryLabel(k)}:</span> {multiplierDisplay(v) || String(v)}
        </span>
      ))}
    </div>
  );
}

function detailList(items: any[] | null | undefined) {
  const labels = cleanList(items);
  if (!labels.length) return null;
  return (
    <ul className="grid list-disc gap-x-6 gap-y-1 pl-4 text-xs leading-5 text-slate-300 sm:grid-cols-2">
      {labels.map((label, i) => (
        <li key={i}>{label}</li>
      ))}
    </ul>
  );
}

function offerPoints(r: CatalogEntry) {
  return r.current_offer_effective ?? r.current_offer_points;
}

function targetPoints(r: CatalogEntry) {
  return r.peak_offer_effective ?? r.peak_offer_points;
}

function currentOfferText(r: CatalogEntry) {
  const points = offerPoints(r);
  if (points) return fmtNum(points);
  if (r.current_offer_cash) return fmtMoney(r.current_offer_cash);
  if (r.eligibility_tags?.includes("closed_to_new_applicants")) return "Closed";
  if (r.last_verified && !r.needs_data) return "No public offer";
  return "Needs data";
}

function currentOfferSubtext(r: CatalogEntry) {
  const parts: string[] = [];
  if (r.current_offer_cash) parts.push(`${fmtMoney(r.current_offer_cash)} cash`);
  if (r.current_offer_min_spend) {
    const window = r.current_offer_window_months ? ` / ${r.current_offer_window_months}mo` : "";
    parts.push(`${fmtMoney(r.current_offer_min_spend)}${window}`);
  }
  return parts;
}

function offerLine(r: CatalogEntry) {
  const parts: string[] = [];
  if (offerPoints(r)) parts.push(`${fmtNum(offerPoints(r))} pts`);
  if (r.current_offer_cash) parts.push(`${fmtMoney(r.current_offer_cash)} cash`);
  if (!offerPoints(r) && r.current_offer_cash) return `${fmtMoney(r.current_offer_cash)} cash`;
  if (parts.length) return parts.join(" + ");
  if (r.eligibility_tags?.includes("closed_to_new_applicants")) return "Closed to new applicants";
  if (r.last_verified && !r.needs_data) return "No public offer";
  return "Needs data";
}

function spendLine(r: CatalogEntry) {
  if (!r.current_offer_min_spend) return "";
  const window = r.current_offer_window_months ? ` in ${r.current_offer_window_months} mo` : "";
  return `${fmtMoney(r.current_offer_min_spend)}${window}`;
}

function referralLine(r: CatalogEntry) {
  const parts: string[] = [];
  if (r.referral_bonus_effective) parts.push(`${fmtNum(r.referral_bonus_effective)} pts`);
  if (r.referral_bonus_cash) parts.push(`${fmtMoney(r.referral_bonus_cash)} cash`);
  return parts.length ? `Referral ${parts.join(" + ")}` : "";
}

function hasKnownOffer(r: CatalogEntry) {
  return Boolean(offerPoints(r) || r.current_offer_cash);
}

function shortDate(value: string | null | undefined) {
  if (!value) return "";
  const parsed = value.includes("T") ? new Date(value) : new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(parsed);
}

function pipelineLabel(items: any[]) {
  return items
    .slice(0, 3)
    .map((item) => `${item.user} ${decisionDisplayStatus(item)}${item.referral_from ? ` via ${item.referral_from}` : ""}`)
    .join(" | ");
}

function mobileSummaryLine(r: CatalogEntry) {
  const peak = fmtNum(targetPoints(r));
  return [offerLine(r), spendLine(r), peak ? `Peak ${peak}` : "", fmtMoney(r.offer_value)].filter(Boolean).join(" | ");
}

function SaveIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4">
      <path
        d="M5 3h12l2 2v16H5V3Zm2 2v5h9V5H7Zm10 14v-7H7v7h10Zm-8-3h6"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function FilterIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4">
      <path
        d="M4 7h10M18 7h2M7 12h13M4 12h1M4 17h7M15 17h5M14 5v4M7 10v4M13 15v4"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function PlanStatusPill({ status }: { status: string }) {
  const style =
    status === "APPLY NOW"
      ? "border-emerald-400/45 bg-emerald-400/10 text-emerald-200"
      : status === "WATCH"
        ? "border-amber-400/45 bg-amber-400/10 text-amber-200"
        : status === "WAIT"
          ? "border-slate-500/50 bg-slate-700/15 text-slate-300"
          : status === "NEEDS DATA"
            ? "border-amber-400/60 border-dashed bg-amber-400/5 text-amber-200"
            : status === "SKIP"
              ? "border-rose-400/50 bg-rose-400/10 text-rose-200"
              : "border-slate-700 bg-slate-900/40 text-slate-400";
  return (
    <span className={`inline-flex h-6 min-w-[6.8rem] items-center justify-center rounded border px-2 text-[11px] font-semibold uppercase tracking-normal ${style}`}>
      {status}
    </span>
  );
}

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="min-w-0 space-y-2">
      <div className="text-[11px] font-semibold uppercase tracking-normal text-slate-500">{title}</div>
      {children}
    </section>
  );
}

function DetailRow({ label, value, hint }: { label: string; value: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[7.5rem_1fr] gap-3 border-b border-slate-800/60 py-1.5 last:border-b-0">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="min-w-0 text-xs text-slate-200">
        <div className="break-words font-medium">{value}</div>
        {hint && <div className="mt-0.5 break-words text-[11px] text-slate-500">{hint}</div>}
      </div>
    </div>
  );
}

function EligibilityText({ card }: { card: CatalogEntry }) {
  if (card.eligibility.eligible) return <span className="text-xs font-medium text-emerald-300">Eligible</span>;
  return (
    <span
      className="cursor-help text-xs font-medium text-rose-300 underline decoration-dotted"
      title={
        card.eligibility.reasons.join(" | ") +
        (card.eligibility.earliest_eligible_date ? ` (earliest ${card.eligibility.earliest_eligible_date})` : "")
      }
    >
      {card.eligibility.block_type === "permanent" ? "Blocked" : "Wait"}
      {card.eligibility.earliest_eligible_date ? ` ${card.eligibility.earliest_eligible_date}` : ""}
    </span>
  );
}

function PhoneMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-ink-400/50 bg-ink-900 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-0.5 break-words text-slate-200">{value}</div>
    </div>
  );
}

export function CardPlan({ user, bump, flash }: { user: string; bump: number; flash: Flash }) {
  const [rows, setRows] = useState<CatalogEntry[]>([]);
  const [householdMoves, setHouseholdMoves] = useState<any[]>([]);
  const [cardReferences, setCardReferences] = useState<CardReference[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [issuer, setIssuer] = useState("");
  const [offerFilter, setOfferFilter] = useState<OfferFilter>("known");
  const [sort, setSort] = useState<SortMode>("ranked");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<CatalogEntry | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const load = () => {
    setLoading(true);
    Promise.allSettled([api.catalog(user), api.household(), api.cardReferences()])
      .then(([catalog, household, references]) => {
        if (catalog.status === "fulfilled") setRows(catalog.value);
        else flash("error", catalog.reason?.message ?? "Card Plan failed to load.");
        if (household.status === "fulfilled") setHouseholdMoves(household.value?.moves ?? []);
        else setHouseholdMoves([]);
        if (references.status === "fulfilled") setCardReferences(references.value);
        else setCardReferences([]);
      })
      .finally(() => setLoading(false));
  };
  useEffect(load, [user, bump]);

  const issuers = useMemo(() => [...new Set(rows.map((r) => r.issuer))].sort(), [rows]);
  const pipelineByProductId = useMemo(() => {
    const out = new Map<number, any[]>();
    for (const move of householdMoves) {
      if (typeof move?.id !== "number") continue;
      const list = out.get(move.id) ?? [];
      list.push(move);
      out.set(move.id, list);
    }
    return out;
  }, [householdMoves]);
  const pipelineFor = (r: CatalogEntry) => pipelineByProductId.get(r.id) ?? [];
  const referencesByKey = useMemo(() => new Map(cardReferences.map((r) => [r.canonical_key, r])), [cardReferences]);
  const sourceUrlFor = (r: CatalogEntry) => {
    const reference = r.canonical_key ? referencesByKey.get(r.canonical_key) : null;
    return reference?.issuer_url || reference?.offer_url || r.source_url || null;
  };

  const filtered = useMemo(() => {
    let next = rows.filter((x) => {
      if (issuer && x.issuer !== issuer) return false;
      if (q) {
        const s = `${x.issuer} ${x.product_name} ${x.display_name ?? ""} ${x.currency ?? ""}`.toLowerCase();
        if (!s.includes(q.toLowerCase())) return false;
      }
      if (offerFilter === "known" && !hasKnownOffer(x)) return false;
      if (offerFilter === "needs_data" && !x.needs_data) return false;
      if (offerFilter === "actionable" && !ACTIONABLE_STATUSES.has(x.status)) return false;
      if (offerFilter === "pipeline" && pipelineFor(x).length === 0) return false;
      return true;
    });

    if (sort === "peak_score") next = [...next].sort((a, b) => b.peak_score - a.peak_score);
    else if (sort === "value") next = [...next].sort((a, b) => b.offer_value - a.offer_value);
    else if (sort === "name") next = [...next].sort((a, b) => cardName(a).localeCompare(cardName(b)));
    else {
      next = [...next].sort((a, b) => {
        const ar = a.rank ?? 9999;
        const br = b.rank ?? 9999;
        if (ar !== br) return ar - br;
        const sa = STATUS_ORDER.indexOf(a.status);
        const sb = STATUS_ORDER.indexOf(b.status);
        if (sa !== sb) return sa - sb;
        return b.offer_value - a.offer_value;
      });
    }
    return next;
  }, [rows, issuer, offerFilter, q, sort, pipelineByProductId]);

  const onSave = async (payload: any) => {
    if (editing) await api.updateProduct(editing.id, payload);
    else await api.createProduct(payload);
    flash("info", editing ? "Product updated." : "Product added to catalog.");
    setFormOpen(false);
    load();
  };

  const onDelete = async (r: CatalogEntry) => {
    if (!confirm(`Delete ${r.issuer} ${cardName(r)} from the catalog?`)) return;
    await api.deleteProduct(r.id);
    flash("info", "Product removed.");
    load();
  };

  const toggleExpanded = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const saveManualOffer = async (r: CatalogEntry, v: number | null, expiresAt: string | null) => {
    try {
      if (v == null) {
        await api.deleteTargetedOffer(r.id, user);
      } else {
        await api.saveTargetedOffer(r.id, user, {
          user,
          issuer: r.issuer,
          product_name: r.product_name,
          product_id: r.id,
          offer_points: v,
          offer_cash: null,
          expires_at: expiresAt,
          notes: null,
        });
      }
      flash("info", v == null ? "Targeted offer cleared." : `Targeted offer set for ${cardName(r)}.`);
      load();
    } catch (e: any) {
      flash("error", e.message);
    }
  };

  if (loading && rows.length === 0) return <Spinner />;

  return (
    <div className="card-plan space-y-3">
      {rows.length === 0 ? (
        <EmptyState
          title="Catalog is empty"
          hint="Use Discover cards to enumerate the universe, then Refresh offers to scrape current and peak offers."
        />
      ) : (
        <>
          <div className="flex flex-col gap-2 md:flex-row md:items-center">
            <div className="input relative h-9 min-w-0 flex-1 py-0 md:max-w-[320px]">
              <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500">
                <FilterIcon />
              </span>
              <input
                className="h-full w-full bg-transparent pl-6 pr-0 text-sm text-slate-100 outline-none placeholder:text-slate-500"
                placeholder="Issuer, card, currency"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-3 gap-2 md:flex md:items-center">
              <select className="input h-9 py-0 md:w-[150px]" value={offerFilter} onChange={(e) => setOfferFilter(e.target.value as OfferFilter)}>
                <option value="known">Known offers</option>
                <option value="all">All cards</option>
                <option value="actionable">Actionable</option>
                <option value="pipeline">In pipeline</option>
                <option value="needs_data">Needs data</option>
              </select>
              <select className="input h-9 py-0 md:w-[145px]" value={issuer} onChange={(e) => setIssuer(e.target.value)}>
                <option value="">All issuers</option>
                {issuers.map((i) => <option key={i}>{i}</option>)}
              </select>
              <select className="input h-9 py-0 md:w-[130px]" value={sort} onChange={(e) => setSort(e.target.value as SortMode)}>
                <option value="ranked">Ranked</option>
                <option value="value">Value</option>
                <option value="peak_score">Peak</option>
                <option value="name">Name</option>
              </select>
            </div>
            <div className="flex items-center justify-between gap-3 md:ml-auto md:justify-end">
              <div className="text-right text-xs text-slate-400">
                {filtered.length} card{filtered.length === 1 ? "" : "s"}
              </div>
              <button
                className="h-9 border border-cyan-accent/40 px-3 text-xs font-semibold text-cyan-accent transition-colors hover:bg-cyan-accent/10"
                onClick={() => {
                  setEditing(null);
                  setFormOpen(true);
                }}
              >
                + Add product
              </button>
            </div>
          </div>

          <div className="space-y-1 md:hidden">
            {filtered.map((r) => (
              <Card
                key={r.id}
                className="cursor-pointer space-y-1 px-2.5 py-2"
                onClick={() => toggleExpanded(r.id)}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-[13px] font-medium leading-5 text-slate-100">{cardName(r)}</div>
                    <div className="text-[11px] text-slate-500">
                      {r.issuer} | {r.ownership}{r.currency ? ` | ${r.currency}` : ""}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <StatusBadge status={decisionDisplayStatus(r)} />
                    {r.is_exceptional && <RareBadge />}
                  </div>
                </div>

                <div className="line-clamp-1 text-[11px] leading-snug text-slate-300">{mobileSummaryLine(r)}</div>

                <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                  {pipelineFor(r).length > 0 && (
                    <span className="chip bg-emerald-400/10 text-emerald-200">Pipeline: {pipelineLabel(pipelineFor(r))}</span>
                  )}
                  {r.targeted_beats_public && (
                    <span className="chip bg-pink-accent/15 text-pink-accent">targeted high</span>
                  )}
                  {referralLine(r) && <span className="chip bg-cyan-accent/10 text-cyan-100">{referralLine(r)}</span>}
                  {useSummary(r) && (
                    <span className="line-clamp-1 min-w-0 text-[11px] text-slate-500">Use: {useSummary(r)}</span>
                  )}
                  {compactList(r.card_benefits) && (
                    <span className="line-clamp-1 min-w-0 text-[11px] text-slate-500">Benefits: {compactList(r.card_benefits)}</span>
                  )}
                </div>

                {expanded.has(r.id) && (
                  <div className="space-y-3 border-t border-ink-400/50 pt-3">
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <PhoneMetric label="Current" value={currentOfferText(r)} />
                      <PhoneMetric label="Target" value={fmtNum(targetPoints(r))} />
                      <PhoneMetric label="Spend" value={spendLine(r) || "none"} />
                      <PhoneMetric label="Value" value={fmtMoney(r.offer_value)} />
                    </div>
                    <ExpandedDetails
                      card={r}
                      pipeline={pipelineFor(r)}
                      sourceUrl={sourceUrlFor(r)}
                      onEdit={() => {
                        setEditing(r);
                        setFormOpen(true);
                      }}
                      onDelete={() => onDelete(r)}
                    />
                  </div>
                )}
              </Card>
            ))}
          </div>

          <div className="card-plan-table-surface hidden overflow-hidden border border-slate-800 bg-ink-900 md:block">
            <table className="w-full table-fixed border-collapse text-left text-sm">
              <colgroup>
                <col style={{ width: "5%" }} />
                <col style={{ width: "29%" }} />
                <col style={{ width: "11%" }} />
                <col style={{ width: "7%" }} />
                <col style={{ width: "12%" }} />
                <col style={{ width: "11%" }} />
                <col style={{ width: "9%" }} />
                <col style={{ width: "10%" }} />
                <col style={{ width: "6%" }} />
              </colgroup>
              <thead className="card-plan-table-head bg-slate-950">
                <tr>
                  <th className="px-3 py-3 text-[11px] font-semibold uppercase tracking-normal text-slate-500">Rank</th>
                  <th className="px-3 py-3 text-[11px] font-semibold uppercase tracking-normal text-slate-500">Card</th>
                  <th className="px-3 py-3 text-[11px] font-semibold uppercase tracking-normal text-slate-500">Status</th>
                  <th className="px-3 py-3 text-[11px] font-semibold uppercase tracking-normal text-slate-500">Peak</th>
                  <th className="px-3 py-3 text-[11px] font-semibold uppercase tracking-normal text-slate-500">Current</th>
                  <th className="px-3 py-3 text-[11px] font-semibold uppercase tracking-normal text-slate-500">Target</th>
                  <th className="px-3 py-3 text-[11px] font-semibold uppercase tracking-normal text-slate-500">Value</th>
                  <th className="px-3 py-3 text-[11px] font-semibold uppercase tracking-normal text-slate-500">Manual Offer</th>
                  <th className="px-3 py-3 text-[11px] font-semibold uppercase tracking-normal text-slate-500">Eligibility</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, index) => {
                  const pipeline = pipelineFor(r);
                  return (
                    <Fragment key={r.id}>
                      <tr
                        className={`card-plan-row cursor-pointer border-t border-slate-800 align-top transition-colors hover:bg-slate-900/70 ${
                          expanded.has(r.id) ? "card-plan-row-open bg-slate-900/60" : "bg-transparent"
                        }`}
                        onClick={() => toggleExpanded(r.id)}
                      >
                        <td className="px-3 py-3 align-top text-sm text-sky-200">
                          {r.rank ?? index + 1}
                        </td>
                        <td className="px-3 py-3 align-top">
                          <div className="flex items-start gap-2">
                            <div className="min-w-0">
                              <div className="truncate text-sm font-semibold leading-5 text-slate-100">{cardName(r)}</div>
                              <div className="mt-0.5 text-[11px] leading-4 text-slate-400">
                                {r.issuer} | {r.ownership}{r.currency ? ` | ${r.currency}` : ""}
                              </div>
                              {referralLine(r) && <div className="mt-1 text-[11px] text-slate-500">{referralLine(r)}</div>}
                              {spendLine(r) && <div className="mt-0.5 text-[11px] text-slate-500">Spend {spendLine(r)}</div>}
                              {pipeline.length > 0 && (
                                <div className="mt-1 text-[11px] text-emerald-200">Pipeline: {pipelineLabel(pipeline)}</div>
                              )}
                              {shortDate(r.last_verified) && (
                                <div className="mt-0.5 text-[11px] text-slate-600">Verified {shortDate(r.last_verified)}</div>
                              )}
                            </div>
                            {r.is_exceptional && <RareBadge />}
                          </div>
                        </td>
                        <td className="px-3 py-3 align-top">
                          <PlanStatusPill status={r.status} />
                        </td>
                        <td className="px-3 py-3 align-top">
                          <div className="text-lg font-semibold leading-none text-slate-100 tabular-nums">{r.needs_data ? "-" : Math.round(r.peak_score)}</div>
                          <div className="mt-1 text-xs text-slate-500">/100</div>
                        </td>
                        <td className="px-3 py-3 align-top">
                          <div className="text-sm text-slate-100 tabular-nums">{currentOfferText(r)}</div>
                          {currentOfferSubtext(r).map((line) => (
                            <div key={line} className="mt-0.5 text-[11px] leading-4 text-slate-500">{line}</div>
                          ))}
                        </td>
                        <td className="px-3 py-3 align-top">
                          <div className="text-sm text-slate-100 tabular-nums">{fmtNum(targetPoints(r))}</div>
                          <div className="mt-0.5 text-[11px] leading-4 text-slate-500">
                            {shortDate(r.peak_offer_date) || shortDate(r.last_verified) || "No date"}
                          </div>
                          {r.targeted_peak_offer_points && r.targeted_peak_offer_points > (r.peak_offer_points ?? 0) && (
                            <div className="mt-0.5 text-[11px] text-pink-accent">targeted high</div>
                          )}
                        </td>
                        <td className="px-3 py-3 align-top">
                          <div className="text-sm font-semibold text-sky-100 tabular-nums">{fmtMoney(r.offer_value)}</div>
                          {r.first_year_credit_value ? (
                            <div className="mt-0.5 text-[11px] text-slate-500">credits {fmtMoney(r.first_year_credit_value)}</div>
                          ) : null}
                        </td>
                        <td className="px-3 py-3 align-top">
                          <ManualOfferCell
                            value={r.manual_targeted_offer?.offer_points ?? null}
                            expiresAt={r.manual_targeted_offer?.expires_at ?? null}
                            onSave={(v, expiresAt) => saveManualOffer(r, v, expiresAt)}
                          />
                        </td>
                        <td className="px-3 py-3 align-top">
                          <EligibilityText card={r} />
                        </td>
                      </tr>
                      {expanded.has(r.id) && (
                        <tr className="card-plan-expanded-row border-t border-slate-800 bg-slate-900/70">
                          <td colSpan={9} className="px-4 py-4">
                            <ExpandedDetails
                              card={r}
                              pipeline={pipeline}
                              sourceUrl={sourceUrlFor(r)}
                              onEdit={() => {
                                setEditing(r);
                                setFormOpen(true);
                              }}
                              onDelete={() => onDelete(r)}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>

          <p className="text-[11px] text-slate-500">
            Manual targeted offers are private per user. Public offers, peaks, benefits, referral bonuses, and verification remain catalog data.
          </p>
        </>
      )}

      <ProductForm open={formOpen} onClose={() => setFormOpen(false)} onSave={onSave} initial={editing} />
    </div>
  );
}

function ExpandedDetails({
  card,
  pipeline,
  sourceUrl,
  onEdit,
  onDelete,
}: {
  card: CatalogEntry;
  pipeline: any[];
  sourceUrl: string | null;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="grid gap-x-8 gap-y-5 text-xs lg:grid-cols-[1.05fr_1fr_1.25fr]">
      <DetailSection title="Offer And Value">
        <div>
          <DetailRow label="Current offer" value={offerLine(card)} hint={spendLine(card) || "No min-spend data"} />
          <DetailRow label="Public peak" value={`${fmtNum(targetPoints(card))} ${card.currency ?? "pts"}`} hint={shortDate(card.peak_offer_date) || "No peak date"} />
          <DetailRow label="First-year value" value={fmtMoney(card.offer_value)} hint={`Credits ${fmtMoney(card.first_year_credit_value)}`} />
          <DetailRow label="Annual fee" value={fmtMoney(card.annual_fee)} hint={card.reports_to_personal_credit ? "Reports personal" : "Business/no personal report"} />
          {referralLine(card) && <DetailRow label="Referral" value={referralLine(card).replace(/^Referral /, "")} />}
          {card.current_offer_cash ? <DetailRow label="Cash offer" value={fmtMoney(card.current_offer_cash)} /> : null}
        </div>
        {card.targeted_beats_public && (
          <div className="text-xs text-pink-accent">Your targeted offer beats the public offer.</div>
        )}
      </DetailSection>

      <DetailSection title="Decision Context">
        <div className="space-y-2 text-xs text-slate-300">
          <div className="flex items-center gap-2">
            <StatusBadge status={decisionDisplayStatus(card)} />
            {card.is_exceptional && <RareBadge />}
          </div>
          <div>
            <span className="text-slate-500">Eligibility:</span>{" "}
            <EligibilityText card={card} />
          </div>
          {decisionCondition(card) && <div className="text-amber-200">{decisionCondition(card)}</div>}
          {card.eligibility.reasons.length > 0 && (
            <ul className="list-disc space-y-1 pl-4 text-slate-400">
              {card.eligibility.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
          {pipeline.length > 0 && (
            <div className="border-l border-emerald-400/50 pl-2 text-emerald-100">
              Pipeline: {pipelineLabel(pipeline)}
            </div>
          )}
          {card.needs_data && (
            <div className="text-amber-200">
              {decisionDataMessage(card)}
            </div>
          )}
          {card.eligibility_tags?.length ? (
            <div className="flex flex-wrap gap-1 pt-1">
              {card.eligibility_tags.map((tag) => (
                <span key={tag} className="rounded border border-slate-800 px-1.5 py-0.5 text-[11px] text-slate-400">{tag}</span>
              ))}
            </div>
          ) : null}
        </div>
      </DetailSection>

      <DetailSection title="Use, Benefits, Source">
        <div className="space-y-3">
          <div>
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-normal text-slate-500">Card Use</div>
            {detailEntries(card.earn_multipliers) ?? detailEntries(card.best_category_uses) ?? (
              <div className="text-xs text-slate-500">No earn/category data yet.</div>
            )}
          </div>
          <div>
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-normal text-slate-500">Benefits</div>
            {detailList(card.card_benefits) ?? <div className="text-xs text-slate-500">No benefits captured yet.</div>}
          </div>
          {detailList(card.downgrade_paths) && (
            <div>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-normal text-slate-500">Downgrade Paths</div>
              {detailList(card.downgrade_paths)}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
            {sourceUrl ? <a href={sourceUrl} target="_blank" rel="noreferrer" className="text-cyan-accent hover:underline">source</a> : "No source URL"}
            {card.last_verified ? <span>verified {shortDate(card.last_verified)}</span> : <span>not verified</span>}
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            <button className="border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:border-cyan-accent/50 hover:text-cyan-accent" onClick={(e) => { e.stopPropagation(); onEdit(); }}>Edit product</button>
            <button className="border border-rose-400/40 px-2 py-1 text-xs text-rose-200 hover:bg-rose-400/10" onClick={(e) => { e.stopPropagation(); onDelete(); }}>Delete</button>
          </div>
        </div>
      </DetailSection>
    </div>
  );
}

function ManualOfferCell({
  value,
  expiresAt,
  onSave,
}: {
  value: number | null;
  expiresAt: string | null;
  onSave: (v: number | null, expiresAt: string | null) => void;
}) {
  const [v, setV] = useState<string>(value == null ? "" : String(value));
  useEffect(() => {
    setV(value == null ? "" : String(value));
  }, [value]);

  const commit = () => {
    const trimmed = v.trim();
    const digits = trimmed.replace(/[^0-9.]/g, "");
    const next = digits === "" ? null : Number(digits);
    if (next !== null && Number.isNaN(next)) return;
    if (next == null) {
      if (value == null) return;
      onSave(null, null);
      return;
    }
    const enteredExpiry = window.prompt("Optional expiry date (YYYY-MM-DD). Leave blank for no expiry.", expiresAt ?? "");
    if (enteredExpiry === null) return;
    const expiry = enteredExpiry.trim() || null;
    if (expiry && !/^\d{4}-\d{2}-\d{2}$/.test(expiry)) {
      window.alert("Use YYYY-MM-DD for the expiry date, or leave it blank.");
      return;
    }
    if (next === value && expiry === expiresAt) return;
    onSave(next, expiry);
  };

  return (
    <div className="flex min-w-0 items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
      <input
        type="text"
        inputMode="numeric"
        className="input h-8 w-20 min-w-0 rounded-none px-2 py-0 text-sm"
        placeholder="points"
        title="Private targeted offer points for this user"
        value={v}
        onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
      />
      <button
        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border border-cyan-accent/50 text-cyan-accent transition-colors hover:bg-cyan-accent/10"
        onClick={commit}
        aria-label="Save manual targeted offer"
        title="Save manual targeted offer"
      >
        <SaveIcon />
      </button>
      {expiresAt && <span className="hidden text-[10px] text-slate-500 xl:inline">exp {expiresAt}</span>}
    </div>
  );
}

function ProductForm({
  open,
  onClose,
  onSave,
  initial,
}: {
  open: boolean;
  onClose: () => void;
  onSave: (payload: any) => Promise<void>;
  initial: CatalogEntry | null;
}) {
  const blank = {
    issuer: "",
    product_name: "",
    product_family: "",
    ownership: "Personal",
    currency: "",
    annual_fee: "",
    current_offer_points: "",
    current_offer_min_spend: "",
    current_offer_window_months: "",
    peak_offer_points: "",
    referral_bonus_override: "",
    first_year_credit_value: "",
    tag: "",
    source_url: "",
  };
  const [f, setF] = useState<any>(initial ? { ...blank, ...initial } : blank);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: any) => setF((p: any) => ({ ...p, [k]: v }));

  useEffect(() => {
    setF(initial ? { ...blank, ...initial } : blank);
    setErr(null);
  }, [initial, open]);

  const submit = async () => {
    if (!f.issuer || !f.product_name) {
      setErr("Issuer and product name are required.");
      return;
    }
    const num = (v: any) => (v === "" || v === null ? null : Number(v));
    setBusy(true);
    try {
      await onSave({
        issuer: f.issuer,
        product_name: f.product_name,
        product_family: f.product_family || null,
        ownership: f.ownership,
        currency: f.currency || null,
        annual_fee: num(f.annual_fee),
        current_offer_points: num(f.current_offer_points),
        current_offer_min_spend: num(f.current_offer_min_spend),
        current_offer_window_months: num(f.current_offer_window_months),
        peak_offer_points: num(f.peak_offer_points),
        referral_bonus_override: num(f.referral_bonus_override),
        first_year_credit_value: num(f.first_year_credit_value),
        tag: f.tag || null,
        source_url: f.source_url || null,
      });
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={initial ? "Edit product" : "Add product"} wide>
      {err && <Banner kind="error">{err}</Banner>}
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Issuer *"><input className="input" value={f.issuer} onChange={(e) => set("issuer", e.target.value)} /></Field>
        <Field label="Product name *"><input className="input" value={f.product_name} onChange={(e) => set("product_name", e.target.value)} /></Field>
        <Field label="Family key"><input className="input" value={f.product_family || ""} onChange={(e) => set("product_family", e.target.value)} placeholder="auto" /></Field>
        <Field label="Ownership">
          <select className="input" value={f.ownership} onChange={(e) => set("ownership", e.target.value)}>
            <option>Personal</option>
            <option>Business</option>
          </select>
        </Field>
        <Field label="Currency"><input className="input" value={f.currency || ""} onChange={(e) => set("currency", e.target.value)} /></Field>
        <Field label="Current offer points"><input type="number" className="input" value={f.current_offer_points} onChange={(e) => set("current_offer_points", e.target.value)} /></Field>
        <Field label="Peak offer points (target)"><input type="number" className="input" value={f.peak_offer_points} onChange={(e) => set("peak_offer_points", e.target.value)} /></Field>
        <Field label="Referral bonus override (manual; referrer earns)"><input type="number" className="input" value={f.referral_bonus_override} onChange={(e) => set("referral_bonus_override", e.target.value)} /></Field>
        <Field label="Min spend ($)"><input type="number" className="input" value={f.current_offer_min_spend} onChange={(e) => set("current_offer_min_spend", e.target.value)} /></Field>
        <Field label="Window (months)"><input type="number" className="input" value={f.current_offer_window_months} onChange={(e) => set("current_offer_window_months", e.target.value)} /></Field>
        <Field label="Annual fee ($)"><input type="number" className="input" value={f.annual_fee} onChange={(e) => set("annual_fee", e.target.value)} /></Field>
        <Field label="First-year credit value ($)"><input type="number" className="input" value={f.first_year_credit_value} onChange={(e) => set("first_year_credit_value", e.target.value)} /></Field>
        <Field label="Tag">
          <select className="input" value={f.tag || ""} onChange={(e) => set("tag", e.target.value)}>
            <option value="">none</option>
            <option value="transferable">transferable</option>
            <option value="hotel_cobrand">hotel_cobrand</option>
            <option value="airline_cobrand">airline_cobrand</option>
            <option value="future_trip">future_trip</option>
          </select>
        </Field>
        <Field label="Source URL"><input className="input" value={f.source_url || ""} onChange={(e) => set("source_url", e.target.value)} /></Field>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" onClick={submit} disabled={busy}>{busy ? "Saving..." : "Save"}</button>
      </div>
    </Modal>
  );
}
