import { useEffect, useMemo, useState } from "react";
import { api, type CardReference, type CatalogEntry, type HeldCard, type ProfileSummary } from "../lib/api";
import type { Flash } from "../App";
import { Card, EmptyState, Modal, Spinner, cardName, fmtDate, fmtMoney, fmtNum } from "../components/ui";
import { CardForm } from "../components/CardForm";
import { buildCurrencyOptions } from "../lib/currencies";
import { five24CardStatusLabel } from "../lib/five24";

const CATEGORY_ORDER = ["Dining", "Groceries", "Travel", "Everyday", "Gas"];

function multiplierLabel(value: unknown) {
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return `${numeric.toLocaleString(undefined, { maximumFractionDigits: 2 })}x`;
  const text = String(value ?? "").trim();
  return text ? (text.toLowerCase().includes("x") ? text : `${text}x`) : "";
}

function categoryUseLabel(item: any) {
  if (!item?.covered) return "Needs coverage";
  const rate = item.multiplier != null ? multiplierLabel(item.multiplier) : "";
  return rate || item.note || "Covered";
}

function shortDate(value: string | null | undefined) {
  if (!value) return "No date";
  return fmtDate(value);
}

function isArchivedCard(card: HeldCard) {
  return ["closed", "cancelled", "canceled"].includes(String(card.status || "").toLowerCase());
}

function currencyOptions(profile: ProfileSummary | null, cards: HeldCard[], catalog: CatalogEntry[]) {
  return buildCurrencyOptions({ profile, cards, catalog });
}

export function MobileProfile({
  user,
  bump,
  flash,
}: {
  user: string;
  bump: number;
  flash: Flash;
}) {
  const [profile, setProfile] = useState<ProfileSummary | null>(null);
  const [cards, setCards] = useState<HeldCard[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [references, setReferences] = useState<CardReference[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [expandedCards, setExpandedCards] = useState<Record<number, boolean>>({});
  const [showArchivedCards, setShowArchivedCards] = useState(false);
  const [cardFormOpen, setCardFormOpen] = useState(false);
  const [editingCard, setEditingCard] = useState<HeldCard | null>(null);
  const [balanceModalOpen, setBalanceModalOpen] = useState(false);
  const [draftCurrency, setDraftCurrency] = useState("");
  const [draftBalance, setDraftBalance] = useState("");

  const load = () => {
    setLoading(true);
    Promise.all([api.profile(user), api.cards(user), api.catalog(user), api.cardReferences()])
      .then(([p, held, cat, refs]) => {
        setProfile(p);
        setCards(held);
        setCatalog(cat);
        setReferences(refs);
      })
      .catch((e) => flash("error", e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, [user, bump]);

  const currencies = useMemo(() => currencyOptions(profile, cards, catalog), [profile, cards, catalog]);
  const balances = profile?.balance_breakdown ?? [];
  const coverage = useMemo(() => {
    const rows = profile?.category_coverage ?? [];
    return [...rows].sort((a: any, b: any) => {
      const ai = CATEGORY_ORDER.findIndex((c) => c.toLowerCase() === String(a.category).toLowerCase());
      const bi = CATEGORY_ORDER.findIndex((c) => c.toLowerCase() === String(b.category).toLowerCase());
      return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
    });
  }, [profile?.category_coverage]);

  const openBalanceModal = (currency?: string, balance?: number | null) => {
    const fallbackCurrency = currency || currencies[0] || "";
    setDraftCurrency(fallbackCurrency);
    setDraftBalance(balance == null ? String(profile?.point_balances?.[fallbackCurrency] ?? "") : String(balance));
    setBalanceModalOpen(true);
  };

  const saveBalance = async () => {
    const currency = draftCurrency.trim();
    if (!currency) {
      flash("warn", "Select a currency first.");
      return;
    }
    setSaving(true);
    try {
      const nextBalances = { ...(profile?.point_balances ?? {}) };
      nextBalances[currency] = Number(draftBalance) || 0;
      const updated = await api.upsertProfile(user, {
        point_balances: nextBalances,
        notes: profile?.notes ?? null,
      });
      setProfile(updated);
      setBalanceModalOpen(false);
      flash("info", "Point balance saved.");
    } catch (e: any) {
      flash("error", e.message);
    } finally {
      setSaving(false);
    }
  };

  const saveHeldCard = async (payload: any) => {
    if (editingCard) {
      await api.updateCard(editingCard.id, payload);
      flash("info", "Card updated.");
    } else {
      await api.createCard(payload);
      flash("info", "Card added.");
    }
    setCardFormOpen(false);
    setEditingCard(null);
    load();
  };

  const openAddCard = () => {
    setEditingCard(null);
    setCardFormOpen(true);
  };

  const openEditCard = (card: HeldCard) => {
    setEditingCard(card);
    setCardFormOpen(true);
  };

  if (loading && !profile) return <Spinner />;

  const f24 = profile?.five_24;
  const activeCards = cards.filter((c) => !isArchivedCard(c));
  const archivedCards = cards.filter(isArchivedCard);
  const visibleCards = showArchivedCards ? archivedCards : activeCards;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-2">
        <Card className="p-3">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">5/24</div>
          <div className={`mt-1 text-xl font-semibold ${f24?.under_524 ? "text-emerald-300" : "text-rose-300"}`}>
            {f24?.count ?? "-"}
          </div>
        </Card>
        <Card className="p-3">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Value</div>
          <div className="mt-1 text-xl font-semibold text-cyan-accent">{fmtMoney(profile?.total_est_value)}</div>
        </Card>
        <Card className="p-3">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Cards</div>
          <div className="mt-1 text-xl font-semibold text-slate-100">{profile?.held_count ?? activeCards.length}</div>
        </Card>
      </div>

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-100">{showArchivedCards ? "Archived Cards" : "Owned Cards"}</h2>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">
              {showArchivedCards ? `${archivedCards.length} closed` : `${activeCards.length} active`}
            </span>
            {archivedCards.length > 0 && (
              <button
                className={`btn-ghost h-7 px-2 text-xs ${showArchivedCards ? "text-cyan-accent" : ""}`}
                onClick={() => setShowArchivedCards((prev) => !prev)}
              >
                {showArchivedCards ? "Active" : "Archive"}
              </button>
            )}
            <button className="mobile-owned-add-button btn-success h-7 px-2 text-xs" onClick={openAddCard}>+</button>
          </div>
        </div>
        {visibleCards.length === 0 ? (
          <EmptyState
            title={showArchivedCards ? "No archived cards" : "No cards on file"}
            hint={showArchivedCards ? "Closed cards will appear here." : "Add held cards from Discover."}
          />
        ) : (
          <div className="space-y-2">
            {visibleCards.map((card) => {
              const isOpen = Boolean(expandedCards[card.id]);
              return (
                <Card key={card.id} className="p-0">
                  <button
                    className="flex w-full items-start justify-between gap-3 px-3 py-3 text-left"
                    onClick={() => setExpandedCards((prev) => ({ ...prev, [card.id]: !prev[card.id] }))}
                  >
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-slate-100">{cardName(card)}</div>
                      <div className="mt-0.5 text-xs text-slate-500">
                        {card.issuer} {card.last4 ? `| ${card.last4}` : ""}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-start gap-2 text-right">
                      <div className="text-[11px] text-slate-400">
                        <div>Opened {shortDate(card.date_opened)}</div>
                        <div>{card.renewal_date ? `Renewal ${shortDate(card.renewal_date)}` : card.status}</div>
                        <div>{card.annual_fee ? `Fee ${fmtMoney(card.annual_fee)}` : "No fee"}</div>
                        <div>{five24CardStatusLabel(card).replace("5/24: ", "")}</div>
                      </div>
                      <span className={`text-lg text-slate-500 transition-transform ${isOpen ? "rotate-90" : ""}`}>&rsaquo;</span>
                    </div>
                  </button>
                  {isOpen && (
                    <div className="space-y-3 border-t border-ink-400/60 px-3 py-3">
                      <div className="grid grid-cols-2 gap-2">
                        <Detail label="Status" value={card.status} />
                        <Detail label="Last 4" value={card.last4 || "-"} />
                        <Detail label="Limit" value={card.credit_limit ? fmtMoney(card.credit_limit) : "-"} />
                        <Detail label="Annual Fee" value={card.annual_fee ? fmtMoney(card.annual_fee) : "None"} />
                        <Detail label="Bonus" value={bonusLine(card)} />
                        <Detail label="Min Spend" value={minSpendLine(card)} />
                        <Detail label="Credit Report" value={five24CardStatusLabel(card)} />
                        <Detail label="Eligible Again" value={card.bonus_eligible_again ? (card.eligible_again_date || "Yes") : "No"} />
                        <Detail label="Targeted" value={card.my_targeted_offer_points ? `${fmtNum(card.my_targeted_offer_points)} pts` : "-"} />
                      </div>
                      {card.notes && <div className="rounded-lg border border-ink-400/60 bg-ink-900 px-3 py-2 text-xs text-slate-400">{card.notes}</div>}
                      <button className="btn-primary h-8 px-3 text-xs" onClick={() => openEditCard(card)}>
                        Edit card
                      </button>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </section>

      <Card className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-100">Point Balances</h2>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">{balances.length} currencies</span>
            <button
              className="inline-flex h-7 items-center justify-center rounded-md border border-emerald-400/40 bg-emerald-400/10 px-2 text-xs font-semibold text-emerald-300"
              onClick={() => openBalanceModal()}
            >
              +
            </button>
          </div>
        </div>
        {balances.length > 0 && (
          <div className="space-y-2">
            {balances.map((row) => (
              <div key={row.currency} className="flex items-center justify-between gap-3 rounded-lg border border-ink-400/60 bg-ink-900 px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-sm text-slate-100">{row.currency}</div>
                  <div className="text-xs font-semibold text-cyan-accent">{fmtNum(row.balance)} pts</div>
                </div>
                <div className="shrink-0 text-xs text-slate-400">{fmtMoney(row.value)}</div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Modal open={balanceModalOpen} onClose={() => setBalanceModalOpen(false)} title="Update Points">
        <div className="space-y-4">
          <div>
            <div className="text-sm font-semibold text-slate-100">{draftCurrency || "Rewards currency"}</div>
            <div className="mt-1 text-xs text-slate-500">Enter the current total balance. This replaces the stored number.</div>
          </div>
          <select className="input text-xs" value={draftCurrency} onChange={(e) => setDraftCurrency(e.target.value)}>
            {currencies.length === 0 && <option value="">Currency</option>}
            {currencies.map((currency) => (
              <option key={currency} value={currency}>{currency}</option>
            ))}
          </select>
          <input
            className="input"
            inputMode="numeric"
            placeholder="Current points balance"
            value={draftBalance}
            onChange={(e) => setDraftBalance(e.target.value)}
          />
          <button className="btn-primary h-10 w-full justify-center" onClick={saveBalance} disabled={saving}>
            Save
          </button>
        </div>
      </Modal>

      {coverage.length > 0 && (
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-100">Card Use</h2>
            <span className="text-xs text-slate-500">Best in your stack</span>
          </div>
          <div className="scrollbar-none flex gap-2 overflow-x-auto pb-1">
            {coverage.map((item: any) => (
              <div key={item.category} className="min-w-[148px] rounded-xl border border-ink-400/60 bg-ink-700/70 p-3">
                <div className="text-[10px] uppercase tracking-wide text-slate-500">{item.category}</div>
                <div className="mt-1 truncate text-sm font-semibold text-slate-100">{item.covered ? cardName(item) : "Needs coverage"}</div>
                <div className="mt-1 text-lg font-semibold text-cyan-accent">{categoryUseLabel(item)}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      <Card>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-100">Value By Currency</h2>
          <span className="text-xs text-slate-500">{fmtMoney(profile?.total_est_value)}</span>
        </div>
        {balances.length === 0 ? (
          <div className="text-sm text-slate-500">No balances saved yet.</div>
        ) : (
          <div className="space-y-2">
            {balances.map((row) => (
              <div key={`value-${row.currency}`} className="space-y-1">
                <div className="flex justify-between gap-2 text-xs">
                  <span className="truncate text-slate-300">{row.currency}</span>
                  <span className="font-semibold text-cyan-accent">{fmtMoney(row.value)}</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-ink-900">
                  <div
                    className="h-full rounded-full bg-cyan-accent"
                    style={{ width: `${Math.min(100, Math.max(4, ((row.value || 0) / Math.max(profile?.total_est_value || 1, 1)) * 100))}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <CardForm
        open={cardFormOpen}
        onClose={() => {
          setCardFormOpen(false);
          setEditingCard(null);
        }}
        onSubmit={saveHeldCard}
        user={user}
        catalog={catalog}
        references={references}
        initial={editingCard}
      />
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-ink-400/60 bg-ink-900 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-0.5 line-clamp-2 text-xs font-medium text-slate-200">{value}</div>
    </div>
  );
}

function bonusLine(card: HeldCard) {
  if (card.welcome_bonus_earned) {
    const amount = card.bonus_points_earned ? `${fmtNum(card.bonus_points_earned)} ${card.bonus_currency ?? "pts"}` : "earned";
    return card.bonus_earned_date ? `${amount} on ${shortDate(card.bonus_earned_date)}` : amount;
  }
  return "Not earned";
}

function minSpendLine(card: HeldCard) {
  if (!card.min_spend_requirement) return "None";
  const progress = fmtMoney(card.min_spend_progress ?? 0);
  const required = fmtMoney(card.min_spend_requirement);
  const due = card.min_spend_deadline ? ` due ${shortDate(card.min_spend_deadline)}` : "";
  return `${progress} / ${required}${due}`;
}
