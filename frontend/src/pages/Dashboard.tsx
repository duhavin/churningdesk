import { useEffect, useMemo, useState } from "react";
import { api, type CardReference, type CatalogEntry, type HeldCard } from "../lib/api";
import type { Flash } from "../App";
import { Card, EmptyState, RareBadge, SectionTitle, Spinner, StatusBadge, cardName, fmtDate, fmtMoney, fmtNum } from "../components/ui";

const SEV: Record<string, string> = {
  high: "border-rose-500/40 bg-rose-500/10 text-rose-200",
  info: "border-cyan-accent/30 bg-cyan-accent/5 text-cyan-100",
};

function attentionAction(item: any) {
  return String(item.action || item.message || "Review item").replace(/\s+/g, " ").trim();
}

function trimAttentionPrefix(value: string) {
  let out = value.trimStart();
  while (out.startsWith("-") || out.startsWith(":")) {
    out = out.slice(1).trimStart();
  }
  return out;
}

function attentionDetail(item: any) {
  const detail = String(item.detail || "").replace(/\s+/g, " ").trim();
  if (detail) return detail;
  const message = String(item.message || "").replace(/\s+/g, " ").trim();
  const action = attentionAction(item);
  return message.startsWith(action) ? trimAttentionPrefix(message.slice(action.length)) : "";
}

type UserDashboard = {
  user: string;
  held_cards: HeldCard[];
  five_24: { count: number; under_524: boolean; earliest_drop_date: string | null };
  needs_attention: any[];
};

export function Dashboard({
  user,
  bump,
  flash,
  onOpenBenefit,
}: {
  user: string;
  bump: number;
  flash: Flash;
  onOpenBenefit?: (user: string) => void;
}) {
  const [users, setUsers] = useState<string[]>([]);
  const [dashboards, setDashboards] = useState<UserDashboard[]>([]);
  const [household, setHousehold] = useState<any>(null);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [references, setReferences] = useState<CardReference[]>([]);
  const [loading, setLoading] = useState(true);
  const [mobileActionTab, setMobileActionTab] = useState<"next" | "referrals">("next");
  const [expandedAction, setExpandedAction] = useState<Record<string, boolean>>({});

  const load = () => {
    setLoading(true);
    api
      .users()
      .then(async (us) => {
        setUsers(us);
        const [ds, hh, cat, refs] = await Promise.all([
          Promise.all(us.map((u) => api.dashboard(u).then((d) => ({ ...d, user: u })))),
          api.household(),
          us[0] ? api.catalog(us[0]) : Promise.resolve([]),
          api.cardReferences(),
        ]);
        setDashboards(ds);
        setHousehold(hh);
        setCatalog(cat);
        setReferences(refs);
      })
      .catch((e) => flash("error", e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, [bump]);

  const cards = useMemo(
    () =>
      dashboards.flatMap((d) =>
        (d.held_cards ?? []).map((card) => ({
          ...card,
          user: d.user,
        })),
      ),
    [dashboards],
  );
  const activeCards = cards.filter((c) => !["closed", "cancelled", "canceled"].includes(String(c.status || "").toLowerCase()));
  const adminUser = users[0];
  const attention = dashboards.flatMap((d) =>
    (d.needs_attention ?? [])
      .filter((a: any) => !["pending_changes", "unreviewed_discovered"].includes(String(a.type ?? "")) || d.user === adminUser)
      .map((a: any) => ({ ...a, user: d.user })),
  );
  const immediateStatuses = new Set(["APPLY NOW", "WATCH"]);
  const moves = (household?.moves ?? [])
    .filter((m: any) => m.decision_ready !== false && immediateStatuses.has(String(m.status)))
    .slice(0, 6);
  const referrals = (household?.referrals ?? [])
    .filter((r: any) => r.decision_ready !== false && ["APPLY NOW", "WATCH"].includes(r.recipient_status))
    .slice(0, 4);
  const annualFees = activeCards.reduce((sum, c) => sum + (c.annual_fee ?? 0), 0);
  const catalogById = useMemo(() => new Map(catalog.map((row) => [row.id, row])), [catalog]);
  // Data freshness: decisions are only as good as the last verification.
  // The refresh pipeline treats >3 days as stale — surface that here so no
  // one has to go check Card Universe to know the data needs a refresh tap.
  const staleness = useMemo(() => {
    if (!catalog.length) return null;
    const now = Date.now();
    const staleMs = 3 * 24 * 60 * 60 * 1000;
    let stale = 0;
    for (const row of catalog) {
      const ts = row.last_verified ? Date.parse(row.last_verified) : NaN;
      if (!Number.isFinite(ts) || now - ts > staleMs) stale += 1;
    }
    return { stale, total: catalog.length };
  }, [catalog]);
  const referenceByKey = useMemo(() => new Map(references.map((row) => [row.canonical_key, row])), [references]);
  const mobileActionTabs = [
    { id: "next", label: "Next", count: moves.length },
    { id: "referrals", label: "Referrals", count: referrals.length },
  ] as const;

  const switchMobileActionTab = (tab: "next" | "referrals") => {
    if (tab === mobileActionTab) return;
    const currentY = window.scrollY;
    setMobileActionTab(tab);
    requestAnimationFrame(() => {
      window.scrollTo({ top: currentY, left: 0, behavior: "auto" });
      requestAnimationFrame(() => window.scrollTo({ top: currentY, left: 0, behavior: "auto" }));
      window.setTimeout(() => window.scrollTo({ top: currentY, left: 0, behavior: "auto" }), 80);
    });
  };

  const toggleExpanded = (key: string) => {
    setExpandedAction((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const pointsForMove = (m: any) => {
    const catalogRow = catalogById.get(m.id);
    const points = m.household_points ?? m.welcome_points ?? m.current_offer_points ?? catalogRow?.current_offer_effective ?? catalogRow?.current_offer_points;
    if (!points) return "Points need data";
    return `${fmtNum(points)} ${m.currency ?? catalogRow?.currency ?? "pts"}`;
  };

  const pointsBreakdownForMove = (m: any) => {
    const catalogRow = catalogById.get(m.id);
    const welcome = m.welcome_points ?? m.current_offer_points ?? catalogRow?.current_offer_effective ?? catalogRow?.current_offer_points;
    const referral = m.referral_bonus_points ?? 0;
    const parts = [];
    if (welcome) parts.push(`${fmtNum(welcome)} welcome`);
    if (referral) parts.push(`${fmtNum(referral)} bonus`);
    if (m.referral_bonus_cash) parts.push(`${fmtMoney(m.referral_bonus_cash)} bonus`);
    return parts.length ? parts.join(" + ") : "Points need data";
  };

  const pointsForReferral = (r: any) => {
    const catalogRow = catalogById.get(r.id);
    const welcome = r.welcome_points ?? r.current_offer_points ?? catalogRow?.current_offer_effective ?? catalogRow?.current_offer_points;
    const referral = r.referral_bonus_points ?? catalogRow?.referral_bonus_effective ?? catalogRow?.referral_bonus_points;
    const parts = [];
    if (welcome) parts.push(`${fmtNum(welcome)} welcome`);
    if (referral) parts.push(`${fmtNum(referral)} bonus`);
    if (r.referral_bonus_cash) parts.push(`${fmtMoney(r.referral_bonus_cash)} bonus`);
    if (welcome && !referral && !r.referral_bonus_cash) parts.push("bonus needs data");
    return parts.length ? parts.join(" + ") : "Points need data";
  };

  const applyUrl = (id: number) => {
    const catalogRow = catalogById.get(id);
    const reference = catalogRow?.canonical_key ? referenceByKey.get(catalogRow.canonical_key) : null;
    return reference?.issuer_url || reference?.offer_url || catalogRow?.source_url || null;
  };

  const benefitAttention = (item: any) => {
    const type = String(item.type ?? "").toLowerCase();
    return Boolean(item.benefit_name || item.benefit_label || type.includes("benefit"));
  };

  const canOpenBenefit = (item: any) => benefitAttention(item) && item.user === user && Boolean(onOpenBenefit);

  const openAttention = (item: any) => {
    if (canOpenBenefit(item) && onOpenBenefit) onOpenBenefit(item.user);
  };

  if (loading && dashboards.length === 0) return <Spinner />;

  return (
    <div className="space-y-6">
      <SectionTitle
        title="Dashboard"
        subtitle="Household overview: what we hold, what needs attention, and what to do next."
      />

      {staleness && staleness.stale > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-amber-300/30 bg-amber-300/5 px-3 py-2 text-xs text-amber-100">
          <span>
            Card data: {staleness.stale} of {staleness.total} cards not verified in the last 3 days —
            recommendations may lag current offers.
          </span>
          <span className="text-amber-200/70">Use “Refresh offers” in the top bar to update.</span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 md:gap-4">
        <Stat label="Active cards" value={String(activeCards.length)} />
        <Stat
          label={household?.wallet ? `Net value after ${fmtMoney(household.wallet.total_annual_fees)} fees` : "Annual fees"}
          value={household?.wallet ? fmtMoney(household.wallet.net_annual_value) : fmtMoney(annualFees)}
          accent={household?.wallet ? (household.wallet.net_annual_value >= 0 ? "good" : "warn") : undefined}
        />
        <Stat label="Needs attention" value={String(attention.length)} accent={attention.length ? "warn" : "good"} />
        <Stat label="Household points value" value={fmtMoney(household?.combined_est_value)} />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="hidden md:block xl:col-span-2">
          <SectionTitle title="Cards" subtitle="Household card status at a glance." />
          {activeCards.length === 0 ? (
            <EmptyState title="No cards yet" hint="Add held cards from a user Profile." />
          ) : (
            <>
            <div className="soft-scroll max-h-[52vh] space-y-1 pr-1 md:hidden">
              {activeCards.map((h) => (
                <Card key={`${h.user}-${h.id}`} className="space-y-0.5 px-2.5 py-1.5">
                  <div className="flex min-w-0 items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-medium leading-5 text-slate-100">{cardName(h)}</div>
                      <div className="text-[11px] text-slate-500">{h.user} | {h.issuer} | {h.ownership}</div>
                    </div>
                    <span className="shrink-0 text-xs text-slate-300">{h.status}</span>
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                    <span>Opened {fmtDate(h.date_opened)}</span>
                    <span>Renewal {h.renewal_date ? fmtDate(h.renewal_date) : "none"}</span>
                    <span>Fee {h.annual_fee ? fmtMoney(h.annual_fee) : "none"}</span>
                  </div>
                  <div className="line-clamp-1 text-[11px] leading-tight text-slate-300">
                    Bonus: {bonusText(h)}
                  </div>
                  {h.welcome_bonus_earned && h.bonus_eligible_again && (
                    <span className="chip bg-cyan-accent/15 text-cyan-accent">re-eligible</span>
                  )}
                </Card>
              ))}
            </div>
            <Card className="dashboard-cards-surface soft-scroll hidden max-h-[520px] overflow-x-auto p-0 md:block">
              <table className="w-full">
                <thead>
                  <tr>
                    <th className="th">Card</th>
                    <th className="th">Opened</th>
                    <th className="th">Renewal</th>
                    <th className="th">Bonus</th>
                    <th className="th">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {activeCards.map((h) => (
                    <tr key={`${h.user}-${h.id}`} className="dashboard-card-row">
                      <td className="td">
                        <div className="font-medium text-slate-100">{cardName(h)}</div>
                        <div className="text-[11px] text-slate-500">
                          {h.user} | {h.issuer} | {h.ownership}
                        </div>
                      </td>
                      <td className="td whitespace-nowrap text-slate-300">{fmtDate(h.date_opened)}</td>
                      <td className="td whitespace-nowrap text-slate-300">
                        {h.renewal_date ? fmtDate(h.renewal_date) : "-"}
                        {h.annual_fee ? <div className="text-[11px] text-slate-500">{fmtMoney(h.annual_fee)}</div> : null}
                      </td>
                      <td className="td text-slate-300">{bonusText(h)}</td>
                      <td className="td">
                        <span className="text-slate-300">{h.status}</span>
                        {h.welcome_bonus_earned && h.bonus_eligible_again && (
                          <span className="ml-1 chip bg-cyan-accent/15 text-cyan-accent">re-eligible</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
            </>
          )}
        </div>

        <div>
          <SectionTitle title="Needs Attention" subtitle="Deadlines, renewals, review queue." />
          {attention.length === 0 ? (
            <EmptyState title="All clear" hint="No renewal, deadline, review, or re-eligibility items right now." />
          ) : (
            <div className="soft-scroll max-h-[360px] space-y-2 pr-1">
              {attention.map((a: any, i: number) => (
                <button
                  key={i}
                  className={`dashboard-attention-row w-full rounded-lg border px-3 py-2 text-left text-sm ${SEV[a.severity] ?? SEV.info} ${
                    canOpenBenefit(a) ? "transition-colors hover:bg-cyan-accent/10" : ""
                  }`}
                  onClick={() => openAttention(a)}
                >
                  <div className="mb-1 flex min-w-0 items-center gap-1.5">
                    <UserPill user={a.user} />
                    <span className="min-w-0 truncate text-[11px] uppercase tracking-wide opacity-70">{a.card}</span>
                  </div>
                  <div className="line-clamp-1 font-medium">{attentionAction(a)}</div>
                  {attentionDetail(a) && <div className="line-clamp-2 text-xs opacity-80">{attentionDetail(a)}</div>}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="xl:hidden">
        <Card className="p-2">
          <div className="grid grid-cols-2 gap-1 rounded-lg bg-ink-900 p-1 text-xs">
            {mobileActionTabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                aria-pressed={mobileActionTab === tab.id}
                className={`rounded-md px-2 py-1.5 font-medium transition-colors ${
                  mobileActionTab === tab.id ? "bg-cyan-accent/15 text-cyan-accent" : "text-slate-400 hover:bg-ink-700"
                }`}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => switchMobileActionTab(tab.id)}
              >
                <span>{tab.label}</span>
                <span className="ml-1 font-semibold opacity-70">{tab.count}</span>
              </button>
            ))}
          </div>
        </Card>

        <Card className="mt-2 min-w-0 p-0">
          {mobileActionTab === "next" ? (
            <>
              <div className="px-3 pt-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Next Applications</div>
              {moves.length === 0 ? (
                <div className="px-3 py-4 text-sm text-slate-500">No APPLY NOW or WATCH applications are ranked right now.</div>
              ) : (
                <div className="space-y-1 px-2.5 py-2">
                  {moves.map((m: any) => {
                    const key = `move-${m.user}-${m.id}`;
                    const url = applyUrl(m.id);
                    const isOpen = Boolean(expandedAction[key]);
                    return (
                    <div key={key} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-2.5 py-1.5">
                      <button className="w-full text-left" onClick={() => toggleExpanded(key)}>
                        <div className="flex min-w-0 items-start justify-between gap-2">
                          <div className="min-w-0 flex-1">
                            <div className="mb-1 flex min-w-0 items-center gap-1.5">
                              <UserPill user={m.user} />
                              <span className="truncate text-[11px] text-slate-500">{m.route ?? (m.referral_from ? `Refer via ${m.referral_from}` : "Direct application")}</span>
                            </div>
                            <div className="truncate text-[13px] font-medium leading-5 text-slate-100">{cardName(m)}</div>
                            <div className="text-[11px] text-slate-500">{m.issuer} | peak {m.peak_score}</div>
                          </div>
                          <div className="flex shrink-0 items-center gap-1">
                            <StatusBadge status={m.status} />
                            {m.is_exceptional && <RareBadge />}
                            <span className={`text-lg text-slate-500 transition-transform ${isOpen ? "rotate-90" : ""}`}>&rsaquo;</span>
                          </div>
                        </div>
                        <div className="mt-1 text-xs font-semibold text-emerald-300">{pointsForMove(m)}</div>
                      </button>
                      {isOpen && (
                        <div
                          className="mt-2 cursor-pointer space-y-2 border-t border-ink-400/50 pt-2 text-xs text-slate-400"
                          onClick={() => toggleExpanded(key)}
                        >
                          <div className="line-clamp-4">{m.reason}</div>
                          <div className="flex flex-wrap gap-x-3 gap-y-1">
                            <span>{m.route ?? (m.referral_from ? `Referral via ${m.referral_from}` : "Direct application")}</span>
                            <span>{pointsBreakdownForMove(m)}</span>
                            <span>Est. {fmtMoney(m.household_value)}</span>
                            <span>Peak {m.peak_score}</span>
                            {m.current_offer_min_spend ? <span>Spend {fmtMoney(m.current_offer_min_spend)} / {m.current_offer_window_months ?? "?"} mo</span> : null}
                          </div>
                          {url && (
                            <a
                              className="btn-success h-8 justify-center px-3 text-xs"
                              href={url}
                              target="_blank"
                              rel="noreferrer"
                              onClick={(event) => event.stopPropagation()}
                            >
                              Apply now
                            </a>
                          )}
                        </div>
                      )}
                    </div>
                  )})}
                </div>
              )}
            </>
          ) : (
            <>
              <div className="px-3 pt-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Referral Actions</div>
              {referrals.length === 0 ? (
                <div className="px-3 py-4 text-sm text-slate-500">No referral route is currently ranked as APPLY NOW or WATCH.</div>
              ) : (
                <div className="space-y-2 px-2.5 py-2">
                  {referrals.map((r: any, i: number) => {
                    const key = `referral-${i}-${r.id}`;
                    const url = applyUrl(r.id);
                    const isOpen = Boolean(expandedAction[key]);
                    return (
                    <div key={key} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-3 py-2">
                      <button className="w-full text-left" onClick={() => toggleExpanded(key)}>
                        <div className="flex min-w-0 items-start justify-between gap-2">
                          <div className="min-w-0 flex-1">
                            <div className="mb-1 flex min-w-0 items-center gap-1.5">
                              <UserPill user={r.from_user} />
                              <span className="truncate text-[11px] text-slate-500">refer {r.to_user}</span>
                            </div>
                            <div className="truncate text-sm font-medium text-slate-100">{cardName(r)}</div>
                            <div className="text-xs font-semibold text-emerald-300">{pointsForReferral(r)}</div>
                          </div>
                          <span className={`shrink-0 text-lg text-slate-500 transition-transform ${isOpen ? "rotate-90" : ""}`}>&rsaquo;</span>
                        </div>
                      </button>
                      {isOpen && (
                        <div
                          className="mt-2 cursor-pointer space-y-2 border-t border-ink-400/50 pt-2 text-xs text-slate-400"
                          onClick={() => toggleExpanded(key)}
                        >
                          <div className="line-clamp-4">{r.reason}</div>
                          <div>Estimated value {fmtMoney(r.household_gain)}</div>
                          {url && (
                            <a
                              className="btn-success h-8 justify-center px-3 text-xs"
                              href={url}
                              target="_blank"
                              rel="noreferrer"
                              onClick={(event) => event.stopPropagation()}
                            >
                              Apply now
                            </a>
                          )}
                        </div>
                      )}
                    </div>
                  )})}
                </div>
              )}
            </>
          )}
        </Card>
      </div>

      <div className="hidden min-w-0 gap-6 xl:grid xl:grid-cols-3">
        <Card className="min-w-0 p-0 xl:col-span-2">
          <div className="px-4 pt-4 pb-2 font-semibold text-slate-100">Next Applications</div>
          {moves.length === 0 ? (
            <div className="px-4 pb-4 text-sm text-slate-500">No APPLY NOW or WATCH applications are ranked right now.</div>
          ) : (
            <div className="soft-scroll max-h-[360px] space-y-2 px-3 pb-3 pr-1">
              {moves.map((m: any) => {
                const key = `desktop-move-${m.user}-${m.id}`;
                const isOpen = Boolean(expandedAction[key]);
                const url = applyUrl(m.id);
                return (
                  <div
                    key={key}
                    className="dashboard-action-row cursor-pointer rounded-lg border border-ink-400/50 px-3 py-2 transition-colors hover:border-ink-400"
                    onClick={() => toggleExpanded(key)}
                  >
                    <div className="flex min-w-0 items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="mb-1 flex min-w-0 items-center gap-1.5">
                          <UserPill user={m.user} />
                          <span className="truncate text-[11px] text-slate-500">{m.route ?? (m.referral_from ? `Refer via ${m.referral_from}` : "Direct application")}</span>
                        </div>
                        <div className="truncate text-sm font-medium text-slate-100">{cardName(m)}</div>
                        <div className="text-[11px] text-slate-500">{m.issuer} | peak {m.peak_score}</div>
                      </div>
                      <div className="flex shrink-0 items-center gap-1.5">
                        <StatusBadge status={m.status} />
                        {m.is_exceptional && <RareBadge />}
                        <span className={`text-lg text-slate-500 transition-transform ${isOpen ? "rotate-90" : ""}`}>&rsaquo;</span>
                      </div>
                    </div>
                    <div className="mt-1 text-sm font-semibold text-cyan-accent">{pointsForMove(m)}</div>
                    {isOpen && (
                      <div className="mt-2 space-y-2 border-t border-ink-400/50 pt-2 text-xs text-slate-400">
                        <div>{m.reason}</div>
                        <div className="flex flex-wrap gap-x-3 gap-y-1">
                          <span>{pointsBreakdownForMove(m)}</span>
                          <span>Est. {fmtMoney(m.household_value)}</span>
                          {m.current_offer_min_spend ? <span>Spend {fmtMoney(m.current_offer_min_spend)} / {m.current_offer_window_months ?? "?"} mo</span> : null}
                        </div>
                        {url && (
                          <a
                            className="btn-success h-8 justify-center px-3 text-xs"
                            href={url}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(event) => event.stopPropagation()}
                          >
                            Apply now
                          </a>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card className="min-w-0 p-0">
          <div className="px-4 pb-2 pt-4 font-semibold text-slate-100">Referral Actions</div>
          {referrals.length === 0 ? (
            <p className="px-4 pb-4 text-sm text-slate-500">No referral route is currently ranked as APPLY NOW or WATCH.</p>
          ) : (
            <div className="soft-scroll max-h-[360px] space-y-2 px-3 pb-3 pr-1">
              {referrals.map((r: any, i: number) => {
                const key = `desktop-referral-${i}-${r.id}`;
                const isOpen = Boolean(expandedAction[key]);
                const url = applyUrl(r.id);
                return (
                  <div
                    key={key}
                    className="dashboard-action-row cursor-pointer rounded-lg border border-ink-400/50 px-3 py-2 transition-colors hover:border-ink-400"
                    onClick={() => toggleExpanded(key)}
                  >
                    <div className="flex min-w-0 items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="mb-1 flex min-w-0 flex-wrap items-center gap-1.5">
                          <UserPill user={r.from_user} />
                          <span className="text-[11px] text-slate-500">refer {r.to_user}</span>
                        </div>
                        <div className="truncate text-sm font-medium text-slate-100">{cardName(r)}</div>
                        <div className="mt-1 text-xs font-semibold text-emerald-300">{pointsForReferral(r)}</div>
                      </div>
                      <span className={`shrink-0 text-lg text-slate-500 transition-transform ${isOpen ? "rotate-90" : ""}`}>&rsaquo;</span>
                    </div>
                    {isOpen && (
                      <div className="mt-2 space-y-2 border-t border-ink-400/50 pt-2 text-xs text-slate-400">
                        <div>{r.reason}</div>
                        <div>Estimated household value {fmtMoney(r.household_gain)}</div>
                        {url && (
                          <a
                            className="btn-success h-8 justify-center px-3 text-xs"
                            href={url}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(event) => event.stopPropagation()}
                          >
                            Apply now
                          </a>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

function bonusText(card: HeldCard) {
  if (card.welcome_bonus_earned) {
    const earned = card.bonus_points_earned ? `${fmtNum(card.bonus_points_earned)} ${card.bonus_currency ?? "pts"}` : "earned";
    return card.bonus_earned_date ? `${earned} | ${fmtDate(card.bonus_earned_date)}` : earned;
  }
  if (card.min_spend_requirement) {
    return `${fmtMoney(card.min_spend_progress ?? 0)} / ${fmtMoney(card.min_spend_requirement)}${card.min_spend_deadline ? ` | due ${fmtDate(card.min_spend_deadline)}` : ""}`;
  }
  return "not earned";
}

function UserPill({ user }: { user: string }) {
  const isUserB = user.toLowerCase() === "user b";
  return (
    <span
      className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
        isUserB
          ? "border-pink-accent/35 bg-pink-accent/15 text-pink-accent"
          : "border-cyan-accent/30 bg-cyan-accent/10 text-cyan-100"
      }`}
    >
      {user}
    </span>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: "good" | "warn";
}) {
  const color = accent === "good" ? "text-emerald-300" : accent === "warn" ? "text-amber-300" : "text-slate-100";
  return (
    <Card className="!px-3 !py-2 md:!p-4">
      <div className="text-[10px] uppercase tracking-wide text-slate-500 md:text-[11px]">{label}</div>
      <div className={`mt-0.5 truncate text-lg font-semibold md:mt-1 md:text-2xl ${color}`}>{value}</div>
    </Card>
  );
}
