import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { Flash } from "../App";
import { Card, EmptyState, RareBadge, SectionTitle, Spinner, StatusBadge, cardName, fmtMoney, fmtNum } from "../components/ui";

const BENEFIT_CATEGORIES = [
  { id: "dining", label: "Dining" },
  { id: "transportation", label: "Transportation" },
  { id: "travel", label: "Travel" },
  { id: "subscriptions", label: "Subscriptions" },
] as const;

function householdBenefitCategory(group: any) {
  const text = [
    group?.benefit_label,
    group?.benefit_name,
    group?.display_name,
    group?.product_name,
    group?.cadence,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  if (/(dining|restaurant|resy|grubhub|doordash|dashpass|dunkin|cheesecake|buffalo wild wings|five guys|uber eats)/.test(text)) {
    return "dining";
  }
  if (/(uber|rideshare|lyft|transit|global entry|tsa|precheck|clear|rental car)/.test(text)) {
    return "transportation";
  }
  if (/(travel|hotel|airline|flight|airport|lounge|priority pass|anniversary|mile|resort|capital one travel)/.test(text)) {
    return "travel";
  }
  if (/(subscription|streaming|apple|walmart|instacart|digital|entertainment|membership)/.test(text)) {
    return "subscriptions";
  }
  return "other";
}

export function Household({ bump, flash }: { user: string; bump: number; flash: Flash }) {
  const [data, setData] = useState<any>(null);
  const [categoryGuide, setCategoryGuide] = useState<any>(null);
  const [benefitLedger, setBenefitLedger] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [overviewTab, setOverviewTab] = useState<"snapshot" | "use" | "benefits">("snapshot");
  const [collapsedBenefitCategories, setCollapsedBenefitCategories] = useState<Record<string, boolean>>({});
  const [expandedActions, setExpandedActions] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.allSettled([api.household(), api.categories(), api.benefits()])
      .then(([household, categories, benefits]) => {
        if (!alive) return;
        if (household.status === "fulfilled") setData(household.value);
        else flash("error", household.reason?.message ?? "Household data failed to load.");
        if (categories.status === "fulfilled") setCategoryGuide(categories.value);
        else setCategoryGuide({ categories: [] });
        if (benefits.status === "fulfilled") setBenefitLedger(benefits.value);
        else setBenefitLedger({ benefits: [], missing: [], summary: {} });
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [bump, flash]);

  const users: any[] = data?.users ?? [];
  const userNames = users.map((u) => u.user);
  const balanceRows: any[] = data?.balance_rows ?? [];
  const cardSnapshot = data?.card_snapshot ?? {};
  const moves: any[] = (data?.moves ?? []).filter((m: any) => m.decision_ready !== false);
  const referrals: any[] = (data?.referrals ?? []).filter((r: any) =>
    r.decision_ready !== false && ["APPLY NOW", "WATCH"].includes(r.recipient_status),
  );
  const categoryOrder = ["dining", "groceries", "travel", "everyday", "gas"];
  const categoryRows: any[] = [...(categoryGuide?.categories ?? [])].sort((a, b) => {
    const ai = categoryOrder.indexOf(a.category);
    const bi = categoryOrder.indexOf(b.category);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });
  const benefitGroups: any[] = benefitLedger?.tracker_groups ?? [];
  const benefitMissing: any[] = benefitLedger?.missing ?? [];
  const categorizedBenefitGroups = useMemo(() => {
    const buckets: Record<string, any[]> = Object.fromEntries(
      [...BENEFIT_CATEGORIES.map((category) => category.id), "other"].map((id) => [id, []]),
    );
    benefitGroups.forEach((group) => {
      buckets[householdBenefitCategory(group)].push(group);
    });
    const sections: { id: string; label: string; groups: any[] }[] = BENEFIT_CATEGORIES.map((category) => ({
      ...category,
      groups: buckets[category.id],
    })).filter((section) => section.groups.length > 0);
    if (buckets.other.length > 0) sections.push({ id: "other", label: "Other", groups: buckets.other });
    return sections;
  }, [benefitGroups]);

  if (loading && !data) return <Spinner />;

  const mobileMoves = moves.slice(0, 8);
  const overviewTabs = [
    { id: "snapshot", label: "Snapshot" },
    { id: "benefits", label: "Benefits" },
    { id: "use", label: "Card Use" },
  ] as const;

  const offerText = (m: any) => {
    if (m.current_offer_points == null) return "-";
    const parts = [`${fmtNum(m.current_offer_points)} ${m.currency ?? "pts"}`];
    if (m.current_offer_min_spend)
      parts.push(`${fmtMoney(m.current_offer_min_spend)} in ${m.current_offer_window_months ?? "?"} mo`);
    return parts.join(" | ");
  };

  const householdValue = (m: any) => m.household_value ?? m.first_year_value ?? m.offer_value ?? 0;

  const householdPoints = (m: any) => m.household_points ?? m.welcome_points ?? m.current_offer_points ?? null;

  const householdPointsLabel = (m: any) => {
    const points = householdPoints(m);
    if (!points) return "Points need data";
    return `${fmtNum(points)} pts`;
  };

  const householdBreakdown = (m: any) => {
    const welcomePoints = m.welcome_points ?? m.current_offer_points ?? null;
    const referralPoints = m.referral_bonus_points ?? 0;
    const referralCash = m.referral_bonus_cash ?? 0;
    const parts = [];
    if (welcomePoints) parts.push(`Welcome ${fmtNum(welcomePoints)} pts`);
    if (referralPoints) parts.push(`bonus ${fmtNum(referralPoints)} pts`);
    if (referralCash) parts.push(`bonus ${fmtMoney(referralCash)}`);
    return parts.length ? parts.join(" + ") : `Value ${fmtMoney(householdValue(m))}`;
  };

  const multiplierText = (value: any) => {
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return `${numeric.toLocaleString(undefined, { maximumFractionDigits: 2 })}x`;
    const text = String(value ?? "").trim();
    return text ? (text.toLowerCase().includes("x") ? text : `${text}x`) : "Needs data";
  };

  const rateText = (item: any) => {
    if (!item) return "Needs data";
    const rate = multiplierText(item.multiplier);
    return rate;
  };

  const compactText = (value: any, fallback = "") => {
    if (value == null) return fallback;
    return String(value).replace(/\s+/g, " ").trim() || fallback;
  };

  const shortDate = (value: any) => {
    const text = String(value || "").trim();
    return text.length >= 10 ? text.slice(5, 10) : text || "no date";
  };

  const progressPct = (value: any) => {
    const pct = Math.round(Math.min(1, Math.max(0, Number(value) || 0)) * 100);
    return `${pct}%`;
  };

  const benefitStatusClass = (status: string) => {
    if (status === "attention") return "border-amber-300/40 bg-amber-300/10 text-amber-100";
    if (status === "needs_data" || status === "needs_source") return "border-pink-accent/40 bg-pink-accent/10 text-pink-100";
    if (status === "done") return "border-emerald-300/30 bg-emerald-300/10 text-emerald-200";
    if (status === "paused") return "border-slate-500/40 bg-slate-500/10 text-slate-400";
    return "border-ink-400 bg-ink-800 text-slate-300";
  };

  const userUsageLine = (user: any) => {
    if (!user?.has_card) return "No card";
    if (user.status === "suppressed" || user.suppressed) return "Paused";
    if (user.amount_available != null) {
      return `${fmtMoney(user.amount_used ?? 0)} / ${fmtMoney(user.amount_available)}`;
    }
    if (["access", "enrollment", "membership"].includes(user.tracking_kind)) {
      return user.status === "confirmed" ? "Confirmed" : "Open";
    }
    return user.display_value || user.action_label || user.status_label || "Track";
  };

  const userRemainingLine = (user: any) => {
    if (!user?.has_card) return "";
    if (user.status === "suppressed" || user.suppressed) return user.timeframe_note || "suppressed";
    if (user.amount_remaining != null) return `${fmtMoney(user.amount_remaining)} left`;
    if (user.timeframe_note) return user.timeframe_note;
    if (user.due_date) return `due ${shortDate(user.due_date)}`;
    return user.status_label || "";
  };

  const toggleBenefitCategory = (id: string) => {
    setCollapsedBenefitCategories((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const toggleAction = (id: string) => {
    setExpandedActions((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const renderBenefitGroup = (group: any) => (
    <div key={group.group_key} className="min-w-0 rounded-md border border-ink-400/70 bg-ink-900 px-2.5 py-2 sm:px-3">
      <div className="flex min-w-0 items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div
            className="line-clamp-1 text-[13px] font-medium leading-snug text-slate-100 sm:text-sm"
            title={compactText(group.benefit_name)}
          >
            {compactText(group.benefit_label, "Benefit")}
          </div>
          <div className="mt-0.5 flex min-w-0 flex-wrap gap-x-2 gap-y-0.5 text-[10px] text-slate-500 sm:text-[11px]">
            <span className="min-w-0 max-w-full truncate">{cardName(group)}</span>
            <span>{group.cadence}</span>
            {group.timeframe_note ? <span>{group.timeframe_note}</span> : group.due_date && <span>due {shortDate(group.due_date)}</span>}
          </div>
        </div>
        <span className={`shrink-0 rounded-md border px-1.5 py-1 text-[10px] font-semibold uppercase leading-none ${benefitStatusClass(group.status)}`}>
          {group.action_label}
        </span>
      </div>

      {group.amount_available != null && (
        <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-ink-500">
          <div className="h-full bg-cyan-accent" style={{ width: progressPct(group.progress) }} />
        </div>
      )}

      <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
        {(group.users ?? []).map((user: any) => (
          <div key={user.user} className="min-w-0 rounded-md bg-ink-800/70 px-2 py-1.5">
            <div className="flex min-w-0 items-center justify-between gap-2">
              <span className={`truncate text-[11px] font-medium ${user.user?.toLowerCase?.() === "user b" ? "text-pink-accent" : "text-slate-200"}`}>
                {user.user}
              </span>
              <span className={`shrink-0 text-[10px] ${user.has_card ? "text-slate-500" : "text-slate-600"}`}>
                {user.status_label}
              </span>
            </div>
            <div className={`mt-0.5 truncate text-[11px] font-semibold ${user.has_card ? "text-cyan-100" : "text-slate-600"}`}>
              {userUsageLine(user)}
            </div>
            {userRemainingLine(user) && (
              <div className="mt-0.5 truncate text-[10px] text-slate-500">{userRemainingLine(user)}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );

  return (
    <div className="flex flex-col gap-6">
      <SectionTitle
        title="Household"
        subtitle="User A + User B optimized together - combined state, the best next move for each of us, and referral synergy."
      />

      <Card className="p-2">
        <div className="grid grid-cols-3 gap-1 rounded-lg bg-ink-900 p-1 text-xs">
          {overviewTabs.map((tab) => (
            <button
              key={tab.id}
              className={`rounded-md px-2 py-1.5 font-medium transition-colors ${
                overviewTab === tab.id ? "bg-cyan-accent/15 text-cyan-accent" : "text-slate-400 hover:bg-ink-700"
              }`}
              onClick={() => setOverviewTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </Card>

      {/* Snapshot + combined balances */}
      {overviewTab === "snapshot" && (
      <>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="lg:p-5">
          <div className="mb-4 flex flex-col gap-1 text-slate-100 sm:flex-row sm:items-center">
            <span className="text-base font-semibold lg:text-lg">Household Snapshot</span>
            <span className="text-xs text-slate-500 sm:ml-auto">
              combined points value {fmtMoney(data?.combined_est_value)}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2.5 lg:gap-4">
            {users.map((u) => (
              <div key={u.user} className="rounded-lg border border-ink-400/50 bg-ink-800/50 px-2.5 py-2.5 lg:px-4 lg:py-4">
                <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between sm:gap-3">
                  <div className={`font-semibold lg:text-lg ${u.user?.toLowerCase?.() === "user b" ? "text-pink-accent" : "text-slate-100"}`}>{u.user}</div>
                  <div className={`font-semibold ${u.under_524 ? "text-emerald-300" : "text-rose-300"} text-xs lg:text-sm`}>
                    {u.five24_count}/5
                  </div>
                </div>
                <div className="mt-1 grid gap-0.5 text-[10px] text-slate-400 sm:flex sm:flex-wrap sm:gap-x-3 sm:gap-y-0.5 lg:mt-2 lg:gap-x-4 lg:text-sm">
                  <span>{fmtNum(u.held_count)} cards</span>
                  <span>{fmtMoney(u.annual_fees)} fees</span>
                  <span>{fmtMoney(u.total_est_value)} value</span>
                </div>
                {!u.under_524 && u.earliest_drop_date && (
                  <div className="mt-1 text-[10px] text-amber-300/80">drops below 5/24 on {u.earliest_drop_date}</div>
                )}
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-0">
          <div className="px-4 pt-4 pb-2 font-semibold text-slate-100">Combined Point Balances</div>
          <div className="soft-scroll max-h-[340px] space-y-2 px-3 pb-3 pr-1 md:hidden">
            {balanceRows.length === 0 ? (
              <div className="rounded-lg border border-ink-400/50 bg-ink-800/40 p-3 text-sm text-slate-500">
                No point balances entered yet.
              </div>
            ) : (
              balanceRows.map((r) => (
                <div key={r.currency} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-3 py-2">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0 truncate text-sm font-medium text-slate-100">{r.currency}</div>
                    <div className="font-semibold text-cyan-accent">{fmtNum(r.combined)}</div>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                    {userNames.map((n) => (
                      <span key={n}>{n} {fmtNum(r.by_user?.[n] ?? 0)}</span>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
          <table className="hidden w-full md:table">
            <thead>
              <tr>
                <th className="th">Currency</th>
                {userNames.map((n) => (
                  <th key={n} className="th text-right">{n}</th>
                ))}
                <th className="th text-right">Combined</th>
              </tr>
            </thead>
            <tbody>
              {balanceRows.length === 0 ? (
                <tr>
                  <td className="td text-slate-500" colSpan={userNames.length + 2}>
                    No point balances entered yet - add them per person in the Profiles tab.
                  </td>
                </tr>
              ) : (
                balanceRows.map((r) => (
                  <tr key={r.currency}>
                    <td className="td text-slate-200">{r.currency}</td>
                    {userNames.map((n) => (
                      <td key={n} className="td text-right tabular-nums text-slate-300">{fmtNum(r.by_user?.[n] ?? 0)}</td>
                    ))}
                    <td className="td text-right font-semibold tabular-nums text-cyan-accent">{fmtNum(r.combined)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </Card>
      </div>

      <Card className="p-0">
        <div className="px-4 pt-4 pb-2 font-semibold text-slate-100">Household Cards Snapshot</div>
        <div className="grid gap-0 md:grid-cols-2">
          {userNames.map((name) => (
            <div key={name} className="border-t border-ink-500/50 md:border-r md:last:border-r-0">
              <div className="px-4 py-2 text-sm font-semibold text-slate-200">{name}</div>
              {(cardSnapshot[name] ?? []).length === 0 ? (
                <div className="px-4 pb-4 text-sm text-slate-500">No active cards.</div>
              ) : (
                <>
                <div className="soft-scroll max-h-[360px] space-y-1 px-2.5 pb-2.5 pr-1 md:hidden">
                  {cardSnapshot[name].map((card: any) => (
                    <div key={card.id} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-2.5 py-1.5">
                      <div className="flex min-w-0 items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[13px] font-medium leading-5 text-slate-100">{cardName(card)}</div>
                          <div className="text-[11px] text-slate-500">{card.issuer}</div>
                        </div>
                        <span className="shrink-0 text-xs text-slate-300">{card.status}</span>
                      </div>
                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                        <span>Opened {card.date_opened ?? "none"}</span>
                        <span>Renewal {card.renewal_date ?? "none"}</span>
                        <span>Fee {card.annual_fee ? fmtMoney(card.annual_fee) : "none"}</span>
                      </div>
                    </div>
                  ))}
                </div>
                <table className="hidden w-full md:table">
                  <thead>
                    <tr>
                      <th className="th">Card</th>
                      <th className="th">Opened</th>
                      <th className="th">Renewal</th>
                      <th className="th">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cardSnapshot[name].map((card: any) => (
                      <tr key={card.id}>
                        <td className="td">
                          <div className="text-slate-100">{cardName(card)}</div>
                          <div className="text-[11px] text-slate-500">{card.issuer}</div>
                        </td>
                        <td className="td text-slate-300">{card.date_opened ?? "-"}</td>
                        <td className="td text-slate-300">
                          {card.renewal_date ?? "-"}
                          {card.annual_fee ? <div className="text-[11px] text-slate-500">{fmtMoney(card.annual_fee)}</div> : null}
                        </td>
                        <td className="td text-slate-300">{card.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </>
              )}
            </div>
          ))}
        </div>
      </Card>
      </>
      )}

      {overviewTab === "use" && (
      <Card className="lg:p-5">
        <div className="mb-4 text-base font-semibold text-slate-100 lg:text-lg">Best Card Per Category</div>
        {categoryRows.length === 0 ? (
          <EmptyState title="No category data" hint="Refresh card details to collect earn multipliers." />
        ) : (
          <div className="grid gap-2 md:grid-cols-5 lg:gap-3">
            {(categoryGuide?.min_spend_windows ?? []).length > 0 && (
              <div className="col-span-full rounded-lg border border-amber-300/40 bg-amber-300/10 px-3 py-2.5">
                <div className="text-xs font-semibold uppercase tracking-wide text-amber-100">Minimum spend first</div>
                {(categoryGuide.min_spend_windows as any[]).map((w) => (
                  <div key={w.held_card_id} className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-amber-50">
                    <span className="font-medium">{w.display_name}</span>
                    <span className="text-amber-200/80">({w.user})</span>
                    <span className="tabular-nums">{`$${Number(w.remaining).toLocaleString()} to go`}</span>
                    {w.days_left != null && <span className="tabular-nums">{w.days_left} days left</span>}
                    {w.daily_needed != null && <span className="tabular-nums">{`~$${Number(w.daily_needed).toLocaleString()}/day`}</span>}
                    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${
                      w.urgency === "critical" || w.urgency === "overdue"
                        ? "border-rose-300/40 bg-rose-300/10 text-rose-100"
                        : w.urgency === "tight"
                          ? "border-amber-300/40 bg-amber-300/10 text-amber-100"
                          : "border-emerald-300/30 bg-emerald-300/10 text-emerald-100"
                    }`}>{w.urgency.replace("_", " ")}</span>
                  </div>
                ))}
                <div className="mt-1.5 text-[11px] text-amber-200/80">
                  Route every purchase to the card above until its minimum spend is done — the welcome bonus outvalues any category multiplier below.
                </div>
              </div>
            )}
            {categoryRows.map((row) => (
              <div key={row.category} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-3 py-2.5 lg:px-4 lg:py-4">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-400 lg:text-sm">{row.category}</div>
                {row.min_spend_override && (
                  <div className="mt-1 rounded border border-amber-300/30 bg-amber-300/5 px-1.5 py-0.5 text-[10px] text-amber-100">
                    Min spend first: {row.min_spend_override.display_name}
                  </div>
                )}
                {row.winner ? (
                  <>
                    <div className="mt-2 truncate text-sm font-medium text-slate-100 lg:text-base">{cardName(row.winner)}</div>
                    <div className="text-[11px] text-slate-500 lg:text-xs">{row.winner.user} - {row.winner.issuer}</div>
                    <div className="mt-2 text-xl font-semibold leading-none text-cyan-100 lg:text-2xl">
                      {rateText(row.winner)}
                      {row.winner.is_fallback && <span className="ml-1 align-middle text-[11px] font-medium text-slate-500">from everyday</span>}
                    </div>
                    {row.runner_up && (
                      <div className="mt-2 truncate text-[11px] text-slate-500 lg:text-xs">
                        Next: {cardName(row.runner_up)} ({rateText(row.runner_up)})
                      </div>
                    )}
                  </>
                ) : (
                  <div className="mt-1 text-xs text-amber-200">{row.needs_data_reason}</div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
      )}

      {overviewTab === "benefits" && (
      <Card>
        <div className="mb-3 flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
          <div>
            <div className="font-semibold text-slate-100">Benefits Quick Tracker</div>
            <div className="text-xs text-slate-500">Household usage by card benefit</div>
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
            <span>left {fmtMoney(benefitLedger?.summary?.known_remaining_value)}</span>
            <span>used {fmtMoney(benefitLedger?.summary?.known_used_value)}</span>
            <span>review {benefitLedger?.summary?.need_action ?? 0}</span>
          </div>
        </div>
        {benefitGroups.length === 0 ? (
          <EmptyState title="No verified benefits yet" hint="Refresh card details to collect benefit data for active cards." />
        ) : (
          <div className="space-y-4">
            {categorizedBenefitGroups.map((section) => (
              <section key={section.id} className="rounded-lg border border-ink-400/60 bg-ink-800/30">
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left"
                  onClick={() => toggleBenefitCategory(section.id)}
                >
                  <div className="min-w-0">
                    <div className="text-[12px] font-semibold uppercase tracking-wide text-emerald-300">{section.label}</div>
                    <div className="mt-0.5 text-[11px] text-slate-500">
                      {section.groups.length} tracked benefit{section.groups.length === 1 ? "" : "s"}
                    </div>
                  </div>
                  <span className={`shrink-0 text-lg text-slate-500 transition-transform ${collapsedBenefitCategories[section.id] ? "" : "rotate-90"}`}>
                    &rsaquo;
                  </span>
                </button>
                {!collapsedBenefitCategories[section.id] && (
                  <div className="grid min-w-0 gap-2 border-t border-ink-400/60 p-2.5 lg:grid-cols-2">
                    {section.groups.map(renderBenefitGroup)}
                  </div>
                )}
              </section>
            ))}
          </div>
        )}
        {benefitMissing.length > 0 && (
          <div className="mt-3 text-xs text-amber-200">
            {benefitMissing.length} active card{benefitMissing.length === 1 ? "" : "s"} need benefit data.
          </div>
        )}
      </Card>
      )}

      {/* Best next applications (both applicants) */}
      <Card className="order-2 hidden p-0 !bg-ink-900/60 md:block">
        <div className="px-4 pt-4 pb-2 font-semibold text-slate-100">Best Household Applications</div>
        {moves.length === 0 ? (
          <EmptyState title="Nothing eligible right now" hint="Run discovery/refresh, or wait for eligibility blocks to clear." />
        ) : (
          <>
          <div className="soft-scroll max-h-[430px] space-y-1 px-2.5 pb-2.5 pr-1 md:hidden">
            {mobileMoves.map((m) => (
              <div key={`${m.user}-${m.id}`} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-2.5 py-1.5">
                <div className="flex min-w-0 items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="text-[11px] uppercase tracking-wide text-slate-500">{m.user} - {m.issuer}</div>
                    <div className="truncate text-[13px] font-medium leading-5 text-slate-100">{cardName(m)}</div>
                    <div className="text-[11px] text-slate-500">{m.ownership}{m.currency ? ` - ${m.currency}` : ""}</div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <StatusBadge status={m.status} />
                    {m.is_exceptional && <RareBadge />}
                  </div>
                </div>
                <div className="line-clamp-1 mt-1 text-[11px] leading-tight text-slate-500">{m.reason}</div>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                  <span>{m.route ?? (m.referral_from ? `Refer via ${m.referral_from}` : "Direct application")}</span>
                  <span className="font-semibold text-emerald-300">{householdPointsLabel(m)}</span>
                  <span className="text-slate-500">{fmtMoney(householdValue(m))}</span>
                  <span className="min-w-0 truncate">{offerText(m)}</span>
                </div>
                <div className="mt-0.5 text-[10px] text-slate-500">{householdBreakdown(m)}</div>
              </div>
            ))}
          </div>
            <div className="soft-scroll hidden max-h-[520px] space-y-2 px-3 pb-3 pr-1 md:block">
              {moves.map((m) => {
                const key = `household-move-${m.user}-${m.id}`;
                const open = Boolean(expandedActions[key]);
                return (
                  <div
                    key={key}
                    className="cursor-pointer rounded-lg border border-ink-400/50 bg-ink-900/60 px-3 py-2 transition-colors hover:border-ink-400"
                    onClick={() => toggleAction(key)}
                  >
                    <div className="flex min-w-0 items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="mb-1 flex flex-wrap items-center gap-1.5">
                          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${m.user?.toLowerCase?.() === "user b" ? "border-pink-accent/35 bg-pink-accent/15 text-pink-accent" : "border-cyan-accent/30 bg-cyan-accent/10 text-cyan-100"}`}>
                            {m.user}
                          </span>
                          <span className="text-[11px] text-slate-500">{m.route ?? (m.referral_from ? `Refer via ${m.referral_from}` : "Direct application")}</span>
                        </div>
                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                          <span className="truncate text-sm font-medium text-slate-100">{cardName(m)}</span>
                          <StatusBadge status={m.status} />
                          {m.is_exceptional && <RareBadge />}
                        </div>
                        <div className="text-[11px] text-slate-500">
                          {m.issuer} - {m.ownership}{m.currency ? ` - ${m.currency}` : ""}
                        </div>
                      </div>
                      <div className="shrink-0 text-right">
                        <div className="font-semibold tabular-nums text-slate-200">{householdPointsLabel(m)}</div>
                        <div className="text-[11px] text-slate-500">{fmtMoney(householdValue(m))}</div>
                      </div>
                      <span className={`shrink-0 text-lg text-slate-500 transition-transform ${open ? "rotate-90" : ""}`}>&rsaquo;</span>
                    </div>
                    {open && (
                      <div className="mt-2 space-y-2 border-t border-ink-400/50 pt-2 text-xs text-slate-400">
                        <div>{m.reason}</div>
                        <div className="flex flex-wrap gap-x-3 gap-y-1">
                          <span>{offerText(m)}</span>
                          <span>{householdBreakdown(m)}</span>
                          <span>{m.route ?? (m.referral_from ? `Refer via ${m.referral_from}` : "Direct application")}</span>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
      </Card>

      {/* Recommended referral actions */}
      <Card className="order-1 hidden md:block">
        <div className="mb-2 font-semibold text-slate-100">Recommended Referral Actions</div>
        {referrals.length === 0 ? (
          <p className="py-4 text-center text-sm text-slate-500">
            No referral route is currently recommended from APPLY NOW or WATCH cards.
          </p>
        ) : (
            <div className="soft-scroll max-h-[360px] space-y-2 pr-1">
            {referrals.map((r, i) => {
              const key = `household-referral-${i}-${r.id}`;
              const open = Boolean(expandedActions[key]);
              return (
              <div
                key={key}
                className="cursor-pointer rounded-lg border border-ink-400/50 bg-ink-900/60 px-3 py-2 transition-colors hover:border-ink-400"
                onClick={() => toggleAction(key)}
              >
                <div className="flex min-w-0 items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="mb-1 flex min-w-0 flex-wrap items-center gap-1.5">
                      <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${r.from_user?.toLowerCase?.() === "user b" ? "border-pink-accent/35 bg-pink-accent/15 text-pink-accent" : "border-cyan-accent/30 bg-cyan-accent/10 text-cyan-100"}`}>
                        {r.from_user}
                      </span>
                      <span className="text-[11px] text-slate-500">refer {r.to_user}</span>
                    </div>
                    <div className="truncate text-sm font-medium text-slate-100">{cardName(r)}</div>
                    <div className="mt-1 text-xs font-semibold text-emerald-300">{householdBreakdown(r)}</div>
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="font-semibold tabular-nums text-emerald-300">{fmtMoney(r.household_gain)}</div>
                    <div className="text-[10px] text-slate-500">household gain</div>
                  </div>
                  <span className={`shrink-0 text-lg text-slate-500 transition-transform ${open ? "rotate-90" : ""}`}>&rsaquo;</span>
                </div>
                {open && (
                  <div className="mt-2 space-y-2 border-t border-ink-400/50 pt-2 text-xs text-slate-400">
                    <div>{r.reason}</div>
                    <div className="flex flex-wrap gap-x-3 gap-y-1">
                      <span>Recipient offer {fmtMoney(r.recipient_offer_value)}</span>
                      <span>Referral value {r.referral_value != null ? fmtMoney(r.referral_value) : "needs data"}</span>
                      <span>Peak {r.peak_score}</span>
                    </div>
                  </div>
                )}
              </div>
            )})}
          </div>
        )}
      </Card>
    </div>
  );
}
