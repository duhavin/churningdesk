import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Flash } from "../App";
import { Card, EmptyState, SectionTitle, Spinner, StatusBadge, fmtMoney, fmtNum } from "../components/ui";

export function Household({ bump, flash }: { user: string; bump: number; flash: Flash }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .household()
      .then(setData)
      .catch((e) => flash("error", e.message))
      .finally(() => setLoading(false));
  }, [bump]);

  if (loading && !data) return <Spinner />;
  const users: any[] = data?.users ?? [];
  const userNames = users.map((u) => u.user);
  const balanceRows: any[] = data?.balance_rows ?? [];
  const cardSnapshot = data?.card_snapshot ?? {};
  const moves: any[] = data?.moves ?? [];
  const referrals: any[] = (data?.referrals ?? []).filter((r: any) =>
    ["APPLY NOW", "WATCH"].includes(r.recipient_status),
  );
  const mobileMoves = moves.slice(0, 8);

  const offerText = (m: any) => {
    if (m.current_offer_points == null) return "—";
    const parts = [`${fmtNum(m.current_offer_points)} ${m.currency ?? "pts"}`];
    if (m.current_offer_min_spend)
      parts.push(`${fmtMoney(m.current_offer_min_spend)} in ${m.current_offer_window_months ?? "?"} mo`);
    return parts.join(" | ");
  };

  return (
    <div className="space-y-6">
      <SectionTitle
        title="Household"
        subtitle="Davin + Marilyn optimized together — combined state, the best next move for each of us, and referral synergy."
      />

      {/* Snapshot + combined balances */}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <div className="mb-3 flex items-center gap-2 text-slate-100">
            <span className="font-semibold">Household Snapshot</span>
            <span className="ml-auto text-xs text-slate-500">
              combined points value {fmtMoney(data?.combined_est_value)}
            </span>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {users.map((u) => (
              <div key={u.user} className="rounded-lg border border-ink-400/50 bg-ink-800/50 px-3 py-2">
                <div className="flex items-baseline justify-between gap-3">
                  <div className="font-semibold text-slate-100">{u.user}</div>
                  <div className={`text-xs font-semibold ${u.under_524 ? "text-emerald-300" : "text-rose-300"}`}>
                    {u.five24_count}/5
                  </div>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                  <span>{fmtNum(u.held_count)} cards</span>
                  <span>{fmtMoney(u.annual_fees)} fees</span>
                  <span>{fmtMoney(u.total_est_value)} points value</span>
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
          <div className="space-y-2 px-3 pb-3 md:hidden">
            {balanceRows.length === 0 ? (
              <div className="rounded-lg border border-ink-400/50 bg-ink-800/40 p-3 text-sm text-slate-500">
                No point balances entered yet.
              </div>
            ) : (
              balanceRows.map((r) => (
                <div key={r.currency} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-3 py-2">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0 truncate text-sm font-medium text-slate-100">{r.currency}</div>
                    <div className="font-mono text-cyan-accent">{fmtNum(r.combined)}</div>
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
                    No point balances entered yet — add them per person in the Profiles tab.
                  </td>
                </tr>
              ) : (
                balanceRows.map((r) => (
                  <tr key={r.currency}>
                    <td className="td text-slate-200">{r.currency}</td>
                    {userNames.map((n) => (
                      <td key={n} className="td text-right font-mono text-slate-300">{fmtNum(r.by_user?.[n] ?? 0)}</td>
                    ))}
                    <td className="td text-right font-mono text-cyan-accent">{fmtNum(r.combined)}</td>
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
                <div className="space-y-2 px-3 pb-3 md:hidden">
                  {cardSnapshot[name].map((card: any) => (
                    <div key={card.id} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-3 py-2">
                      <div className="flex min-w-0 items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium text-slate-100">{card.product_name}</div>
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
                          <div className="text-slate-100">{card.product_name}</div>
                          <div className="text-[11px] text-slate-500">{card.issuer}</div>
                        </td>
                        <td className="td text-slate-300">{card.date_opened ?? "—"}</td>
                        <td className="td text-slate-300">
                          {card.renewal_date ?? "—"}
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

      {/* Best next applications (both applicants) */}
      <Card className="p-0">
        <div className="px-4 pt-4 pb-2 font-semibold text-slate-100">Best Next Applications</div>
        {moves.length === 0 ? (
          <EmptyState title="Nothing eligible right now" hint="Run discovery/refresh, or wait for eligibility blocks to clear." />
        ) : (
          <>
          <div className="space-y-2 px-3 pb-3 md:hidden">
            {mobileMoves.map((m) => (
              <div key={`${m.user}-${m.id}`} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-3 py-2">
                <div className="flex min-w-0 items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-[11px] uppercase tracking-wide text-slate-500">{m.user} · {m.issuer}</div>
                    <div className="truncate text-sm font-medium text-slate-100">{m.product_name}</div>
                    <div className="text-[11px] text-slate-500">{m.ownership}{m.currency ? ` · ${m.currency}` : ""}</div>
                  </div>
                  <StatusBadge status={m.status} />
                </div>
                <div className="line-clamp-1 mt-1 text-[11px] leading-tight text-slate-500">{m.reason}</div>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                  <span>{m.referral_from ? `via ${m.referral_from}` : "Direct"}</span>
                  <span className="font-mono text-emerald-300">{fmtMoney(m.first_year_value)}</span>
                  <span className="min-w-0 truncate">{offerText(m)}</span>
                </div>
              </div>
            ))}
          </div>
          <table className="hidden w-full md:table">
            <thead>
              <tr>
                <th className="th">Applicant</th>
                <th className="th">Card</th>
                <th className="th">Route</th>
                <th className="th">Current offer</th>
                <th className="th text-right">First-year value</th>
              </tr>
            </thead>
            <tbody>
              {moves.map((m) => (
                <tr key={`${m.user}-${m.id}`} className="hover:bg-ink-600/40 align-top">
                  <td className="td font-medium text-slate-100">{m.user}</td>
                  <td className="td">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-100">{m.product_name}</span>
                      <StatusBadge status={m.status} />
                    </div>
                    <div className="text-[11px] text-slate-500">
                      {m.issuer} · {m.ownership}
                      {m.currency ? ` · ${m.currency}` : ""}
                    </div>
                    <div className="mt-0.5 text-[11px] text-slate-500">{m.reason}</div>
                  </td>
                  <td className="td text-slate-300">
                    {m.referral_from ? (
                      <span className="text-cyan-accent">
                        Refer via {m.referral_from}
                        {m.referral_value != null ? ` (+${fmtMoney(m.referral_value)})` : ""}
                      </span>
                    ) : (
                      "Direct application"
                    )}
                  </td>
                  <td className="td text-slate-300">{offerText(m)}</td>
                  <td className="td text-right font-mono text-slate-200">{fmtMoney(m.first_year_value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </>
        )}
      </Card>

      {/* Recommended referral actions */}
      <Card>
        <div className="mb-2 font-semibold text-slate-100">Recommended Referral Actions</div>
        {referrals.length === 0 ? (
          <p className="py-4 text-center text-sm text-slate-500">
            No referral route is currently recommended from APPLY NOW or WATCH cards.
          </p>
        ) : (
          <div className="space-y-2">
            {referrals.map((r, i) => (
              <div key={i} className="flex flex-col gap-1.5 rounded-lg border border-ink-400/50 bg-ink-800/40 px-3 py-2 sm:flex-row sm:items-start sm:gap-3">
                <span className="chip w-fit bg-cyan-accent/15 text-cyan-accent">{r.from_user} → {r.to_user}</span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-slate-100">{r.product_name}</div>
                  <div className="line-clamp-2 mt-0.5 text-[11px] leading-tight text-slate-400">{r.reason}</div>
                </div>
                <div className="shrink-0 text-left sm:text-right">
                  <div className="font-mono text-sm text-emerald-300">{fmtMoney(r.household_gain)}</div>
                  <div className="text-[10px] text-slate-500">household gain</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
