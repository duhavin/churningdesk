import { useEffect, useMemo, useState } from "react";
import { api, type HeldCard } from "../lib/api";
import type { Flash } from "../App";
import { Card, EmptyState, SectionTitle, Spinner, StatusBadge, fmtMoney, fmtNum } from "../components/ui";

const SEV: Record<string, string> = {
  high: "border-rose-500/40 bg-rose-500/10 text-rose-200",
  info: "border-cyan-accent/30 bg-cyan-accent/5 text-cyan-100",
};

type UserDashboard = {
  user: string;
  held_cards: HeldCard[];
  five_24: { count: number; under_524: boolean; earliest_drop_date: string | null };
  needs_attention: any[];
};

export function Dashboard({ bump, flash }: { user: string; bump: number; flash: Flash }) {
  const [users, setUsers] = useState<string[]>([]);
  const [dashboards, setDashboards] = useState<UserDashboard[]>([]);
  const [household, setHousehold] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    api
      .users()
      .then(async (us) => {
        setUsers(us);
        const [ds, hh] = await Promise.all([
          Promise.all(us.map((u) => api.dashboard(u).then((d) => ({ ...d, user: u })))),
          api.household(),
        ]);
        setDashboards(ds);
        setHousehold(hh);
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
  const activeCards = cards.filter((c) => c.status !== "Closed");
  const attention = dashboards.flatMap((d) => (d.needs_attention ?? []).map((a: any) => ({ ...a, user: d.user })));
  const moves = (household?.moves ?? []).slice(0, 6);
  const referrals = (household?.referrals ?? []).filter((r: any) => ["APPLY NOW", "WATCH"].includes(r.recipient_status)).slice(0, 4);
  const annualFees = activeCards.reduce((sum, c) => sum + (c.annual_fee ?? 0), 0);

  if (loading && dashboards.length === 0) return <Spinner />;

  return (
    <div className="space-y-6">
      <SectionTitle
        title="Dashboard"
        subtitle="Household overview: what we hold, what needs attention, and what to do next."
      />

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 md:gap-4">
        <Stat label="Active cards" value={String(activeCards.length)} />
        <Stat label="Annual fees" value={fmtMoney(annualFees)} />
        <Stat label="Needs attention" value={String(attention.length)} accent={attention.length ? "warn" : "good"} />
        <Stat label="Household points value" value={fmtMoney(household?.combined_est_value)} />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <SectionTitle title="Cards" subtitle="Household card status at a glance." />
          {activeCards.length === 0 ? (
            <EmptyState title="No cards yet" hint="Add held cards from a user Profile." />
          ) : (
            <>
            <div className="space-y-1.5 md:hidden">
              {activeCards.map((h) => (
                <Card key={`${h.user}-${h.id}`} className="space-y-1 px-3 py-2">
                  <div className="flex min-w-0 items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-slate-100">{h.product_name}</div>
                      <div className="text-[11px] text-slate-500">{h.user} · {h.issuer} · {h.ownership}</div>
                    </div>
                    <span className="shrink-0 text-xs text-slate-300">{h.status}</span>
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                    <span>Opened {h.date_opened}</span>
                    <span>Renewal {h.renewal_date ?? "none"}</span>
                    <span>Fee {h.annual_fee ? fmtMoney(h.annual_fee) : "none"}</span>
                  </div>
                  <div className="line-clamp-2 text-[11px] leading-tight text-slate-300">
                    Bonus: {bonusText(h)}
                  </div>
                  {h.welcome_bonus_earned && h.bonus_eligible_again && (
                    <span className="chip bg-cyan-accent/15 text-cyan-accent">re-eligible</span>
                  )}
                </Card>
              ))}
            </div>
            <Card className="hidden overflow-x-auto p-0 md:block">
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
                    <tr key={`${h.user}-${h.id}`} className="hover:bg-ink-600/40">
                      <td className="td">
                        <div className="font-medium text-slate-100">{h.product_name}</div>
                        <div className="text-[11px] text-slate-500">
                          {h.user} · {h.issuer} · {h.ownership}
                        </div>
                      </td>
                      <td className="td text-slate-300">{h.date_opened}</td>
                      <td className="td text-slate-300">
                        {h.renewal_date ?? "—"}
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
            <div className="space-y-2">
              {attention.map((a: any, i: number) => (
                <div key={i} className={`rounded-lg border px-3 py-2 text-sm ${SEV[a.severity] ?? SEV.info}`}>
                  <div className="text-[11px] uppercase tracking-wide opacity-70">
                    {a.user} · {a.card}
                  </div>
                  <div>{a.message}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="grid min-w-0 gap-6 lg:grid-cols-2">
        <Card className="min-w-0 p-0">
          <div className="px-4 pt-4 pb-2 font-semibold text-slate-100">Next Applications</div>
          {moves.length === 0 ? (
            <div className="px-4 pb-4 text-sm text-slate-500">No eligible next applications are ranked yet.</div>
          ) : (
            <>
            <div className="space-y-2 px-3 pb-3 md:hidden">
              {moves.map((m: any) => (
                <div key={`${m.user}-${m.id}`} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-3 py-2">
                  <div className="flex min-w-0 items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="text-[11px] uppercase tracking-wide text-slate-500">{m.user} · {m.referral_from ? `via ${m.referral_from}` : "direct"}</div>
                      <div className="truncate text-sm font-medium text-slate-100">{m.product_name}</div>
                      <div className="text-[11px] text-slate-500">{m.issuer} · peak {m.peak_score}</div>
                    </div>
                    <StatusBadge status={m.status} />
                  </div>
                  <div className="mt-1 font-mono text-sm text-emerald-300">{fmtMoney(m.household_value)}</div>
                </div>
              ))}
            </div>
            <table className="hidden w-full md:table">
              <thead>
                <tr>
                  <th className="th">User</th>
                  <th className="th">Card</th>
                  <th className="th">Route</th>
                  <th className="th">Status</th>
                  <th className="th text-right">Value</th>
                </tr>
              </thead>
              <tbody>
                {moves.map((m: any) => (
                  <tr key={`${m.user}-${m.id}`}>
                    <td className="td text-slate-100">{m.user}</td>
                    <td className="td">
                      <div className="text-slate-100">{m.product_name}</div>
                      <div className="text-[11px] text-slate-500">{m.issuer} · peak {m.peak_score}</div>
                    </td>
                    <td className="td text-slate-300">{m.referral_from ? `Refer via ${m.referral_from}` : "Direct"}</td>
                    <td className="td"><StatusBadge status={m.status} /></td>
                    <td className="td text-right font-mono text-slate-200">{fmtMoney(m.household_value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </>
          )}
        </Card>

        <Card className="min-w-0">
          <div className="mb-2 font-semibold text-slate-100">Referral Actions</div>
          {referrals.length === 0 ? (
            <p className="text-sm text-slate-500">No referral route is currently ranked as APPLY NOW or WATCH.</p>
          ) : (
            <div className="space-y-2">
              {referrals.map((r: any, i: number) => (
                <div key={i} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-3 py-2">
                  <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                    <span className="chip bg-cyan-accent/15 text-cyan-accent">{r.from_user} → {r.to_user}</span>
                    <span className="min-w-0 truncate text-sm font-medium text-slate-100">{r.product_name}</span>
                  </div>
                  <div className="mt-1 text-xs text-slate-400">
                    Applicant welcome offer plus referrer bonus: {fmtMoney(r.household_gain)}
                  </div>
                </div>
              ))}
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
    return card.bonus_earned_date ? `${earned} · ${card.bonus_earned_date}` : earned;
  }
  if (card.min_spend_requirement) {
    return `${fmtMoney(card.min_spend_progress ?? 0)} / ${fmtMoney(card.min_spend_requirement)}${card.min_spend_deadline ? ` · due ${card.min_spend_deadline}` : ""}`;
  }
  return "not earned";
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
