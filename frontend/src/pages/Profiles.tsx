import { useEffect, useState } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { api, type CatalogEntry, type HeldCard } from "../lib/api";
import type { Flash } from "../App";
import { Banner, Card, EmptyState, SectionTitle, Spinner, fmtMoney, fmtNum } from "../components/ui";
import { CardForm } from "../components/CardForm";

const PIE_COLORS = ["#22d3ee", "#f472b6", "#a78bfa", "#34d399", "#fbbf24", "#60a5fa", "#fb7185"];

export function Profiles({ user, bump, flash }: { user: string; bump: number; flash: Flash }) {
  const [profile, setProfile] = useState<any>(null);
  const [cards, setCards] = useState<HeldCard[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [balances, setBalances] = useState<{ currency: string; balance: string }[]>([]);
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<HeldCard | null>(null);

  const load = () => {
    setLoading(true);
    Promise.all([api.profile(user), api.cards(user), api.catalog(user)])
      .then(([p, c, cat]) => {
        setProfile(p);
        setCards(c);
        setCatalog(cat);
        setBalances(Object.entries(p.point_balances ?? {}).map(([currency, balance]) => ({ currency, balance: String(balance) })));
        setNotes(p.notes ?? "");
      })
      .catch((e) => flash("error", e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, [user, bump]);

  const currencyOptions = Array.from(
    new Set(
      [
        ...catalog.map((c) => c.currency),
        ...cards.map((c) => c.bonus_currency),
        ...balances.map((b) => b.currency),
        "cash back",
      ]
        .filter(Boolean)
        .map((x) => String(x)),
    ),
  ).sort();

  const save = async () => {
    setSaving(true);
    try {
      const point_balances: Record<string, number> = {};
      for (const b of balances) {
        if (b.currency.trim()) point_balances[b.currency.trim()] = Number(b.balance) || 0;
      }
      await api.upsertProfile(user, { point_balances, notes });
      flash("info", "Profile saved.");
      load();
    } catch (e: any) {
      flash("error", e.message);
    } finally {
      setSaving(false);
    }
  };

  const onSubmitCard = async (payload: any) => {
    if (editing) await api.updateCard(editing.id, payload);
    else await api.createCard(payload);
    flash("info", editing ? "Card updated." : "Card added.");
    setFormOpen(false);
    setEditing(null);
    load();
  };

  const onDeleteCard = async (card: HeldCard) => {
    if (!confirm(`Delete ${card.issuer} ${card.product_name}?`)) return;
    await api.deleteCard(card.id);
    flash("info", "Card deleted.");
    load();
  };

  const onStatusCard = async (card: HeldCard, status: string) => {
    await api.updateCard(card.id, { status });
    flash("info", status === "Closed" ? "Card marked closed." : status === "Active" ? "Card reopened." : `Card marked ${status.toLowerCase()}.`);
    load();
  };

  if (loading && !profile) return <Spinner />;

  const f24 = profile?.five_24;
  const positiveBalances = profile?.balance_breakdown?.filter((b: any) => b.value > 0) ?? [];
  const balanceBreakdown = profile?.balance_breakdown ?? [];
  const categoryCoverage = profile?.category_coverage ?? [];
  const coveredCategories = categoryCoverage.filter((c: any) => c.covered).length;

  return (
    <div className="space-y-6">
      <SectionTitle title={`${user}'s Profile`} subtitle="Detailed user record: cards, limits, min-spend, point balances, and eligibility inputs." />

      <div className="grid gap-3 sm:grid-cols-3 md:gap-4">
        <Card>
          <div className="text-[11px] uppercase tracking-wide text-slate-500">5/24</div>
          <div className={`mt-1 text-2xl font-semibold ${f24?.under_524 ? "text-emerald-300" : "text-rose-300"}`}>
            {f24?.count ?? "-"} / 24
          </div>
          <div className="text-xs text-slate-500">{f24?.under_524 ? "Under 5/24" : "At/over 5/24"}</div>
          {f24?.earliest_drop_date && <div className="mt-1 text-xs text-slate-400">Drops below 5 on {f24.earliest_drop_date}</div>}
        </Card>
        <Card>
          <div className="text-[11px] uppercase tracking-wide text-slate-500">Total est. value</div>
          <div className="mt-1 text-2xl font-semibold text-cyan-accent">{fmtMoney(profile?.total_est_value)}</div>
          <div className="text-xs text-slate-500">Manual balances x cpp</div>
        </Card>
        <Card>
          <div className="text-[11px] uppercase tracking-wide text-slate-500">Cards on file</div>
          <div className="mt-1 text-2xl font-semibold text-slate-100">{profile?.held_count ?? cards.length}</div>
        </Card>
      </div>

      {categoryCoverage.length > 0 && (
        <Card>
          <div className="mb-3 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <div className="text-[11px] uppercase tracking-wide text-slate-500">Use cards</div>
              <div className="text-sm text-slate-300">
                {coveredCategories}/{categoryCoverage.length} covered
              </div>
            </div>
            <div className="text-xs text-slate-500">From Card Plan earn data</div>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
            {categoryCoverage.map((item: any) => (
              <div key={item.category} className="rounded-md border border-ink-400/70 bg-ink-900 px-3 py-2">
                <div className="text-[11px] uppercase tracking-wide text-slate-500">{item.category}</div>
                {item.covered ? (
                  <>
                    <div className="mt-1 text-sm font-medium text-slate-100">{item.product_name}</div>
                    <div className="text-[11px] text-slate-500">{item.issuer}</div>
                    <div className="mt-1 text-xs text-cyan-100">
                      {item.multiplier ? `${item.multiplier}x` : "covered"}
                      {item.note ? ` - ${item.note}` : ""}
                    </div>
                  </>
                ) : (
                  <div className="mt-1 text-sm text-amber-200">Needs coverage</div>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid gap-6 xl:grid-cols-5">
        <div className="xl:col-span-2">
          <SectionTitle title="Point Balances" subtitle="Encrypted at rest. Currencies come from known cards." />
          <Card className="space-y-3">
            <div className="space-y-2">
              {balances.map((b, i) => (
                <div key={i} className="grid grid-cols-[minmax(0,1fr)_108px_34px] gap-2 sm:grid-cols-[minmax(0,1fr)_140px_36px]">
                  <select
                    className="input min-w-0"
                    value={b.currency}
                    onChange={(e) => setBalances((p) => p.map((x, j) => (j === i ? { ...x, currency: e.target.value } : x)))}
                  >
                    <option value="">Currency</option>
                    {currencyOptions.map((currency) => (
                      <option key={currency} value={currency}>{currency}</option>
                    ))}
                  </select>
                  <input
                    className="input min-w-0 text-right"
                    type="number"
                    placeholder="0"
                    value={b.balance}
                    onChange={(e) => setBalances((p) => p.map((x, j) => (j === i ? { ...x, balance: e.target.value } : x)))}
                  />
                  <button
                    className="btn-ghost h-9 justify-center px-0"
                    aria-label="Remove balance"
                    onClick={() => setBalances((p) => p.filter((_, j) => j !== i))}
                  >
                    x
                  </button>
                </div>
              ))}
            </div>
            <button className="btn-ghost w-full justify-center sm:w-auto" onClick={() => setBalances((p) => [...p, { currency: currencyOptions[0] ?? "", balance: "" }])}>
              Add currency
            </button>
            <div>
              <span className="label mt-2">Notes</span>
              <textarea className="input min-h-20" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </div>
            <div className="flex justify-end">
              <button className="btn-primary w-full justify-center sm:w-auto" onClick={save} disabled={saving}>{saving ? "Saving..." : "Save profile"}</button>
            </div>
          </Card>

          {positiveBalances.length > 0 && (
            <Card className="mt-4">
              <div className="mb-2 text-[11px] uppercase tracking-wide text-slate-500">Value by currency</div>
              <div className="h-[180px] sm:h-[200px]">
                <ResponsiveContainer>
                  <PieChart>
                    <Pie data={positiveBalances} dataKey="value" nameKey="currency" innerRadius={42} outerRadius={76} paddingAngle={2} stroke="none">
                      {positiveBalances.map((_: any, i: number) => (
                        <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip
                      formatter={(v: any) => fmtMoney(v as number)}
                      contentStyle={{ background: "#10151f", border: "1px solid #2a3447", borderRadius: 8, fontSize: 12 }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </Card>
          )}

          {balanceBreakdown.length > 0 && (
            <>
              <div className="mt-4 space-y-2 md:hidden">
                {balanceBreakdown.map((b: any, i: number) => (
                  <Card key={`${b.currency}-${i}`} className="space-y-1 px-3 py-2">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-slate-100">{b.currency}</div>
                        <div className="text-[11px] text-slate-500">
                          {fmtNum(b.balance)} pts - {b.cpp ? `${b.cpp} cpp` : "No valuation"}
                        </div>
                      </div>
                      <div className="shrink-0 text-right font-mono text-cyan-accent">{fmtMoney(b.value)}</div>
                    </div>
                  </Card>
                ))}
              </div>
              <Card className="mt-4 hidden overflow-x-auto p-0 md:block">
                <table className="w-full">
                  <thead>
                    <tr>
                      <th className="th">Currency</th>
                      <th className="th">Balance</th>
                      <th className="th">cpp</th>
                      <th className="th">Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {balanceBreakdown.map((b: any, i: number) => (
                      <tr key={i}>
                        <td className="td text-slate-200">{b.currency}</td>
                        <td className="td font-mono text-slate-300">{fmtNum(b.balance)}</td>
                        <td className="td font-mono text-slate-400">{b.cpp ? `${b.cpp}c` : "-"}</td>
                        <td className="td font-mono text-slate-200">{fmtMoney(b.value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            </>
          )}
        </div>

        <div className="xl:col-span-3">
          <SectionTitle
            title="Cards"
            subtitle="Detailed held-card records. Last 4 and credit limit are encrypted at rest."
            right={
              <button
                className="btn-primary w-full justify-center sm:w-auto"
                onClick={() => {
                  setEditing(null);
                  setFormOpen(true);
                }}
              >
                Add card
              </button>
            }
          />

          {f24?.contributing?.length > 0 ? (
            <Card className="mb-4">
              <div className="mb-2 text-[11px] uppercase tracking-wide text-slate-500">Counting toward 5/24</div>
              <ul className="space-y-1 text-sm">
                {f24.contributing.map((c: any, i: number) => (
                  <li key={i} className="flex justify-between gap-3">
                    <span className="min-w-0 text-slate-200">{c.issuer} {c.product_name}</span>
                    <span className="shrink-0 text-slate-500">{c.date_opened}</span>
                  </li>
                ))}
              </ul>
            </Card>
          ) : (
            <Banner kind="info">No personal-credit-reporting cards in the trailing 24 months.</Banner>
          )}

          {cards.length === 0 ? (
            <EmptyState title="No cards yet" hint="Add a held card to start tracking eligibility and deadlines." />
          ) : (
            <>
              <div className="mt-4 space-y-2 md:hidden">
                {cards.map((c) => (
                  <Card key={c.id} className="space-y-1.5 px-3 py-2">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-slate-100">{c.product_name}</div>
                        <div className="text-[11px] text-slate-500">{c.issuer} - {c.ownership}</div>
                      </div>
                      <span className="shrink-0 text-xs text-slate-300">{c.status}</span>
                    </div>
                    <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                      <span>Last 4 {c.last4 ? `..${c.last4}` : "none"}</span>
                      <span>Opened {c.date_opened}</span>
                      <span>Renewal {c.renewal_date ?? "none"}</span>
                      <span>Limit {fmtMoney(c.credit_limit)}</span>
                    </div>
                    <div className="line-clamp-2 text-[11px] leading-tight text-slate-300">
                      Min spend: {c.min_spend_requirement ? `${fmtMoney(c.min_spend_progress ?? 0)} / ${fmtMoney(c.min_spend_requirement)}` : "none"}
                      <span className="text-slate-500"> | </span>
                      Bonus: {bonusText(c)}
                    </div>
                    <CardActions
                      card={c}
                      className="border-t border-ink-500/60 pt-1.5"
                      onEdit={() => {
                        setEditing(c);
                        setFormOpen(true);
                      }}
                      onStatus={onStatusCard}
                      onDelete={onDeleteCard}
                    />
                  </Card>
                ))}
              </div>
              <div className="mt-4 hidden space-y-2 md:block">
                {cards.map((c) => (
                  <Card key={c.id} className="px-3 py-2">
                    <div className="grid gap-3 lg:grid-cols-[minmax(220px,1.4fr)_minmax(170px,0.8fr)_minmax(220px,1fr)_auto] lg:items-center">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-slate-100">{c.product_name}</div>
                        <div className="text-[11px] text-slate-500">{c.issuer} - {c.ownership}</div>
                        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                          <span>Last 4 {c.last4 ? `..${c.last4}` : "none"}</span>
                          <span>Status {c.status}</span>
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                        <span>Opened</span>
                        <span className="text-slate-300">{c.date_opened}</span>
                        <span>Renewal</span>
                        <span className="text-slate-300">{c.renewal_date ?? "none"}</span>
                        <span>Limit</span>
                        <span className="font-mono text-slate-300">{fmtMoney(c.credit_limit)}</span>
                      </div>

                      <div className="text-[11px] text-slate-400">
                        <div>
                          Min spend:{" "}
                          <span className="text-slate-300">
                            {c.min_spend_requirement ? `${fmtMoney(c.min_spend_progress ?? 0)} / ${fmtMoney(c.min_spend_requirement)}` : "none"}
                          </span>
                        </div>
                        <div>
                          Deadline: <span className="text-slate-300">{c.min_spend_completed ? "complete" : c.min_spend_deadline ?? "none"}</span>
                        </div>
                        <div>
                          Bonus: <span className="text-slate-300">{bonusText(c)}</span>
                        </div>
                      </div>

                      <CardActions
                        card={c}
                        className="lg:justify-end"
                        onEdit={() => {
                          setEditing(c);
                          setFormOpen(true);
                        }}
                        onStatus={onStatusCard}
                        onDelete={onDeleteCard}
                      />
                    </div>
                  </Card>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      <CardForm
        open={formOpen}
        onClose={() => {
          setFormOpen(false);
          setEditing(null);
        }}
        onSubmit={onSubmitCard}
        user={user}
        catalog={catalog}
        initial={editing}
      />
    </div>
  );
}

function bonusText(card: HeldCard) {
  if (card.welcome_bonus_earned) {
    const earned = card.bonus_points_earned ? `${fmtNum(card.bonus_points_earned)} ${card.bonus_currency ?? "pts"}` : "earned";
    return card.bonus_earned_date ? `${earned} - ${card.bonus_earned_date}` : earned;
  }
  if (card.min_spend_requirement) {
    return `${fmtMoney(card.min_spend_progress ?? 0)} / ${fmtMoney(card.min_spend_requirement)}`;
  }
  return "not earned";
}

function CardActions({
  card,
  className = "",
  onEdit,
  onStatus,
  onDelete,
}: {
  card: HeldCard;
  className?: string;
  onEdit: () => void;
  onStatus: (card: HeldCard, status: string) => void;
  onDelete: (card: HeldCard) => void;
}) {
  const closed = card.status === "Closed";
  const cancelPending = card.status === "Cancel Pending";

  return (
    <div className={`flex flex-wrap items-center justify-end gap-x-3 gap-y-1 text-xs ${className}`}>
      <button className="text-slate-400 hover:text-cyan-accent" onClick={onEdit}>
        edit
      </button>
      {!closed && !cancelPending && (
        <button className="text-slate-400 hover:text-amber-300" onClick={() => onStatus(card, "Cancel Pending")}>
          plan cancel
        </button>
      )}
      {!closed ? (
        <button className="text-slate-400 hover:text-pink-accent" onClick={() => onStatus(card, "Closed")}>
          mark cancelled
        </button>
      ) : (
        <button className="text-slate-400 hover:text-emerald-300" onClick={() => onStatus(card, "Active")}>
          reopen
        </button>
      )}
      <button className="text-slate-500 hover:text-pink-accent" onClick={() => onDelete(card)}>
        delete
      </button>
    </div>
  );
}
