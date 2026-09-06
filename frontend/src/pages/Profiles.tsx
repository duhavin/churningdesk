import { useEffect, useMemo, useState } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Sector, Tooltip } from "recharts";
import { api, type CardReference, type CatalogEntry, type HeldCard } from "../lib/api";
import type { Flash } from "../App";
import { Banner, Card, EmptyState, SectionTitle, Spinner, cardName, fmtDate, fmtMoney, fmtNum } from "../components/ui";
import { CardForm } from "../components/CardForm";
import { buildCurrencyOptions } from "../lib/currencies";
import { five24CardStatusLabel } from "../lib/five24";

const PIE_COLORS = ["#0e7490", "#be185d", "#7c3aed", "#047857", "#b45309", "#475569", "#0369a1"];

function StablePieSector(props: any) {
  return <Sector {...props} className="profile-balance-sector" focusable="false" tabIndex={-1} stroke="var(--chart-stroke)" strokeWidth={2} />;
}

function BalanceTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload ?? {};
  const color = row.pieColor ?? row.fill ?? payload[0]?.color ?? "#22d3ee";

  return (
    <div className="rounded-md border border-ink-400 bg-ink-900 px-3 py-2 text-xs shadow-xl">
      <div className="flex items-center gap-2 font-medium text-slate-100">
        <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
        <span>{row.currency}</span>
      </div>
      <div className="mt-1 space-y-0.5 text-[11px] text-slate-300">
        <div>{fmtNum(row.balance)} pts</div>
        <div>{row.cpp ? `${row.cpp} cpp` : "No valuation"}</div>
        <div className="text-cyan-accent">{fmtMoney(row.value)}</div>
      </div>
    </div>
  );
}

function benefitStatusClass(status: string, priority?: string) {
  if (status === "suppressed" || priority === "paused") return "border-slate-500/40 bg-slate-500/10 text-slate-400";
  if (priority === "attention") return "border-amber-300/40 bg-amber-300/10 text-amber-100";
  if (priority === "needs_data" || priority === "needs_source") return "border-pink-accent/40 bg-pink-accent/10 text-pink-100";
  if (status === "confirmed") return "border-emerald-300/30 bg-emerald-300/10 text-emerald-200";
  if (status === "unconfirmed") return "border-slate-500/40 bg-slate-500/10 text-slate-300";
  if (status === "used") return "border-emerald-300/30 bg-emerald-300/10 text-emerald-200";
  if (status === "partial") return "border-cyan-accent/30 bg-cyan-accent/10 text-cyan-100";
  if (status === "unused") return "border-amber-300/30 bg-amber-300/10 text-amber-100";
  if (status === "upcoming") return "border-cyan-accent/30 bg-cyan-accent/10 text-cyan-accent";
  return "border-ink-400 bg-ink-800 text-slate-400";
}

function verifiedLabel(row: any) {
  if (row.verified_status === "verified") return "verified";
  if (row.verified_status === "stale") return "stale";
  return "needs source";
}

function statusLabel(status: string) {
  if (status === "suppressed") return "paused";
  return String(status || "unknown").replace(/_/g, " ");
}

function cleanBenefitName(value: any) {
  return String(value || "Benefit")
    .replace(/\[(?:text|title|meta|json-ld|table)\]\s*/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function compactBenefitName(row: any) {
  const label = String(row?.benefit_label || "").trim();
  if (label) return label;
  const cleaned = cleanBenefitName(row?.benefit_name);
  return cleaned.length > 48 ? `${cleaned.slice(0, 45).trim()}...` : cleaned;
}

function benefitSourceClass(row: any) {
  if (row.verified_status === "verified") return "text-emerald-300/80";
  if (row.verified_status === "stale") return "text-amber-200/80";
  return "text-pink-200/80";
}

function dateShort(value: any) {
  const text = String(value || "").trim();
  if (!text) return "no date";
  return text.length >= 10 ? text.slice(5, 10) : text;
}

function daysLabel(row: any) {
  if (row.days_remaining == null) return row.due_date ? dateShort(row.due_date) : "no date";
  const days = Number(row.days_remaining);
  if (!Number.isFinite(days)) return row.due_date ? dateShort(row.due_date) : "no date";
  if (days < 0) return "expired";
  if (days === 0) return "today";
  if (days === 1) return "1 day";
  return `${days} days`;
}

function benefitValueLabel(row: any) {
  if (row.status === "suppressed") return "paused";
  if (isBinaryBenefit(row)) {
    if (row.tracking_kind === "enrollment") return "registration";
    if (row.tracking_kind === "membership") return "membership";
    return "access";
  }
  if (row.amount_available != null) return fmtMoney(row.amount_available);
  return row.display_value || "needs data";
}

function benefitRemainingLabel(row: any) {
  if (row.status === "suppressed") return "paused";
  if (isBinaryBenefit(row)) return row.status === "confirmed" ? "done" : "open";
  if (row.is_anniversary) return daysLabel(row);
  if (row.amount_remaining != null) return fmtMoney(row.amount_remaining);
  return row.display_value || "needs data";
}

function isBinaryBenefit(row: any) {
  return ["access", "enrollment", "membership"].includes(row?.tracking_kind);
}

type BenefitEdit = {
  amount_available: string;
  amount_used: string;
  add_amount: string;
  notes: string;
  suppressed?: boolean;
  suppress_all?: boolean;
};

function needsAmountInput(row: any) {
  return row.status === "needs_amount" || row.amount_source === "manual_usage";
}

function multiplierLabel(value: any) {
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return `${numeric.toLocaleString(undefined, { maximumFractionDigits: 2 })}x`;
  const text = String(value ?? "").trim();
  return text ? (text.toLowerCase().includes("x") ? text : `${text}x`) : "";
}

function categoryUseLabel(item: any) {
  if (!item?.covered) return "";
  const rate = item.multiplier != null ? multiplierLabel(item.multiplier) : "";
  const note = String(item.note ?? "").trim();
  const normalizedRate = rate.toLowerCase().replace(/\s+/g, "");
  const normalizedNote = note.toLowerCase().replace(/\s+/g, "");
  const base = rate || note || "covered";
  const detail = note && normalizedNote !== normalizedRate && !rate ? note : base;
  return detail;
}

function balancesToRecord(rows: { currency: string; balance: string }[]) {
  const point_balances: Record<string, number> = {};
  for (const row of rows) {
    const currency = row.currency.trim();
    if (currency) point_balances[currency] = Number(row.balance) || 0;
  }
  return point_balances;
}

function numberOrNull(value: string, fallback: number | null = null) {
  const trimmed = value.trim();
  if (trimmed === "") return fallback;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function cappedBenefitUsed(value: number | null, available: number | null) {
  if (value == null) return null;
  const used = Math.max(0, value);
  return available == null ? used : Math.min(used, Math.max(0, available));
}

function isArchivedCard(card: HeldCard) {
  return ["closed", "cancelled", "canceled"].includes(String(card.status || "").toLowerCase());
}


function BenefitsTracker({
  rows,
  missing,
  summary,
  edits,
  savingKey,
  onEdit,
  onSave,
}: {
  rows: any[];
  missing: any[];
  summary: any;
  edits: Record<string, BenefitEdit>;
  savingKey: string | null;
  onEdit: (rowKey: string, patch: Partial<BenefitEdit>) => void;
  onSave: (row: any, override?: Partial<BenefitEdit>) => void;
}) {
  const [expandedRows, setExpandedRows] = useState<Record<string, boolean>>({});
  const [showAll, setShowAll] = useState(false);
  const attentionRows = useMemo(
    () => rows.filter((row) => row.priority === "attention" || row.priority === "needs_data" || row.status === "needs_amount"),
    [rows],
  );
  const standardRows = useMemo(() => {
    const attentionKeys = new Set(attentionRows.map((row) => row.row_key));
    return rows.filter((row) => !attentionKeys.has(row.row_key));
  }, [attentionRows, rows]);
  const remainingValue =
    summary?.known_remaining_value ??
    rows.reduce((total, row) => total + (row.status === "unused" || row.status === "partial" ? Number(row.amount_remaining || 0) : 0), 0);
  const pausedCount = summary?.suppressed ?? rows.filter((row) => row.status === "suppressed" || row.suppressed).length;
  const activeCount = Math.max(rows.length - pausedCount, 0);
  const needsReview =
    summary?.needs_review ??
    rows.filter((row) => row.priority === "needs_data" || row.priority === "needs_source" || row.verified_status !== "verified").length;
  const hasExpandedBenefit = Object.values(expandedRows).some(Boolean);
  const benefitGridClass = showAll || hasExpandedBenefit
    ? "grid gap-2 pr-1 lg:grid-cols-2"
    : "soft-scroll grid max-h-[56vh] gap-2 pr-1 lg:max-h-[620px] lg:grid-cols-2";
  const missingGridClass = showAll
    ? "grid gap-1.5 pr-1 sm:grid-cols-2"
    : "soft-scroll grid max-h-[260px] gap-1.5 pr-1 sm:grid-cols-2";

  const renderBenefit = (row: any) => {
    const edit = edits[row.row_key] ?? {
      amount_available: row.amount_available == null ? "" : String(row.amount_available),
      amount_used: row.amount_used == null ? "" : String(row.amount_used),
      add_amount: "",
      notes: "",
    };
    const progress = row.progress == null ? 0 : Math.min(100, Math.max(0, Math.round(row.progress * 100)));
    const expanded = Boolean(expandedRows[row.row_key]);
    const showAmountInput = needsAmountInput(row);
    const binaryBenefit = isBinaryBenefit(row);
    const binaryConfirmed = row.status === "confirmed";
    const paused = row.status === "suppressed" || row.suppressed;

    return (
      <div
        key={row.row_key}
        className={`rounded-md border bg-ink-900 px-2.5 py-2 sm:px-3 ${
          paused
            ? "border-slate-500/30 opacity-80"
            : row.priority === "attention"
            ? "border-amber-300/40"
            : row.priority === "needs_data" || row.priority === "needs_source"
              ? "border-pink-accent/30"
              : "border-ink-400/70"
        }`}
      >
        <div className="flex min-w-0 items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div
              className="line-clamp-1 text-[13px] font-medium leading-snug text-slate-100"
              title={cleanBenefitName(row.benefit_name)}
            >
              {compactBenefitName(row)}
            </div>
            <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px] text-slate-500 sm:text-[11px]">
              <span className="max-w-full truncate">{cardName(row)}</span>
              <span>{row.cadence}</span>
              {row.timeframe_note ? <span>{row.timeframe_note}</span> : <span>due {dateShort(row.due_date)}</span>}
            </div>
          </div>
          <span
            className={`shrink-0 rounded-md border px-1.5 py-1 text-[10px] font-semibold uppercase leading-none ${benefitStatusClass(
              row.status,
              row.priority,
            )}`}
          >
            {row.action_label || row.status_label || statusLabel(row.status)}
          </span>
        </div>

        <div className="mt-1.5 grid grid-cols-3 gap-1.5 text-[10px] sm:gap-2 sm:text-[11px]">
          <div className="min-w-0 rounded-md bg-ink-800/70 px-2 py-1">
            <div className="text-slate-500">{row.is_anniversary ? "Value" : binaryBenefit ? "Type" : "Available"}</div>
            <div className="truncate tabular-nums text-slate-200">{benefitValueLabel(row)}</div>
          </div>
          <div className="min-w-0 rounded-md bg-ink-800/70 px-2 py-1">
            <div className="text-slate-500">{row.is_anniversary || binaryBenefit ? "Status" : "Used"}</div>
            <div className="truncate tabular-nums text-cyan-accent">
              {row.is_anniversary || binaryBenefit ? statusLabel(row.status) : fmtMoney(row.amount_used)}
            </div>
          </div>
          <div className="min-w-0 rounded-md bg-ink-800/70 px-2 py-1">
            <div className="text-slate-500">{row.is_anniversary || binaryBenefit ? "Due" : "Left"}</div>
            <div className="truncate tabular-nums text-slate-200">{benefitRemainingLabel(row)}</div>
          </div>
        </div>

        {row.amount_available != null && (
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-ink-500">
            <div className="h-full bg-cyan-accent" style={{ width: `${progress}%` }} />
          </div>
        )}

        <div className="mt-1.5 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[10px] sm:text-[11px]">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-slate-500">
            <span className={benefitSourceClass(row)}>{verifiedLabel(row)}</span>
            {row.amount_source === "manual_usage" && <span className="text-cyan-100">manual amount</span>}
            {row.last_verified && <span>{row.last_verified.slice(0, 10)}</span>}
            {row.source_url && (
              <a className="hover:text-cyan-accent" href={row.source_url} target="_blank" rel="noreferrer">
                source
              </a>
            )}
          </div>
          {binaryBenefit && !paused && (
            <button
              className={binaryConfirmed ? "btn-ghost h-7 px-2 py-0.5 text-[11px]" : "btn-primary h-7 px-2 py-0.5 text-[11px]"}
              disabled={savingKey === row.row_key}
              onClick={() => onSave(row, { amount_used: binaryConfirmed ? "0" : "1" })}
            >
              {savingKey === row.row_key ? "..." : binaryConfirmed ? "Open" : "Confirm"}
            </button>
          )}
          <button
            className="shrink-0 text-[11px] font-medium text-slate-400 hover:text-cyan-accent"
            disabled={savingKey === row.row_key}
            onClick={() =>
              onSave(row, {
                suppressed: !paused,
                suppress_all: true,
                notes: !paused ? (edit.notes.trim() || row.notes || "Paused") : (edit.notes.trim() || row.notes || ""),
              })
            }
          >
            {savingKey === row.row_key ? "..." : paused ? "resume" : "pause"}
          </button>
          <button
            className="shrink-0 text-[11px] font-medium text-slate-400 hover:text-cyan-accent"
            onClick={() => setExpandedRows((prev) => ({ ...prev, [row.row_key]: !prev[row.row_key] }))}
          >
            {expanded ? "close" : row.status === "used" ? "edit" : "track"}
          </button>
        </div>

        {expanded && (
          <div className="mt-3 rounded-md border border-ink-400/60 bg-ink-800/50 p-2">
            <div
              className={`grid gap-2 ${
                binaryBenefit
                  ? "sm:grid-cols-[minmax(0,1fr)_96px]"
                  : showAmountInput
                    ? "sm:grid-cols-[96px_96px_96px_minmax(0,1fr)_64px_56px]"
                    : "sm:grid-cols-[96px_96px_minmax(0,1fr)_64px_56px]"
              }`}
            >
              {showAmountInput && !binaryBenefit && (
                <input
                  className="input h-8 min-w-0 px-2 text-right text-xs"
                  type="number"
                  min="0"
                  placeholder="available"
                  value={edit.amount_available}
                  onChange={(e) => onEdit(row.row_key, { amount_available: e.target.value })}
                />
              )}
              {!binaryBenefit && (
                <input
                  className="input h-8 min-w-0 px-2 text-right text-xs"
                  type="number"
                  min="0"
                  placeholder="add used"
                  value={edit.add_amount}
                  onChange={(e) => onEdit(row.row_key, { add_amount: e.target.value })}
                />
              )}
              {!binaryBenefit && (
                <input
                  className="input h-8 min-w-0 px-2 text-right text-xs"
                  type="number"
                  min="0"
                  placeholder="set total"
                  value={edit.amount_used}
                  onChange={(e) => onEdit(row.row_key, { amount_used: e.target.value })}
                />
              )}
              <input
                className="input h-8 min-w-0 px-2 text-xs"
                placeholder="note"
                value={edit.notes}
                onChange={(e) => onEdit(row.row_key, { notes: e.target.value })}
              />
              <button
                className="btn-primary h-8 justify-center px-2 text-xs"
                disabled={savingKey === row.row_key}
                onClick={() =>
                  onSave(
                    row,
                    paused
                      ? { suppressed: false, suppress_all: true, notes: edit.notes.trim() || row.notes || "" }
                      : binaryBenefit
                        ? { amount_used: binaryConfirmed ? "0" : "1" }
                        : undefined,
                  )
                }
              >
                {savingKey === row.row_key ? "..." : paused ? "Resume" : binaryBenefit ? (binaryConfirmed ? "Open" : "Confirm") : "Add"}
              </button>
              {!binaryBenefit && (
                <button
                  className="btn-ghost h-8 justify-center px-2 text-xs"
                  disabled={savingKey === row.row_key}
                  onClick={() => onSave(row, { amount_used: edit.amount_used })}
                >
                  Set
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <Card className="space-y-3">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500">Benefits</div>
          <div className="text-sm text-slate-300">What needs attention first. Full ledger stays one tap away.</div>
        </div>
        <div className="text-xs text-slate-500">
          {activeCount} active{pausedCount > 0 ? `, ${pausedCount} paused` : ""}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <div className="rounded-md border border-ink-400/70 bg-ink-900 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">To do</div>
          <div className="mt-1 text-lg font-semibold text-amber-100">{summary?.attention ?? attentionRows.length}</div>
        </div>
        <div className="rounded-md border border-ink-400/70 bg-ink-900 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Value left</div>
          <div className="mt-1 text-lg font-semibold text-cyan-accent">{fmtMoney(remainingValue)}</div>
        </div>
        <div className="rounded-md border border-ink-400/70 bg-ink-900 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Review</div>
          <div className="mt-1 text-lg font-semibold text-pink-100">{needsReview}</div>
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState title="No benefits loaded yet" hint="Run Refresh Offers or Deep Refresh so active cards can pull verified benefit data." />
      ) : (
        <div className="space-y-3">
          {attentionRows.length > 0 && (
            <div>
              <div className="mb-2 flex items-center justify-between text-xs">
                <span className="font-medium uppercase tracking-wide text-amber-100">Use or confirm next</span>
                <span className="text-slate-500">{attentionRows.length}</span>
              </div>
              <div className={benefitGridClass}>{attentionRows.map(renderBenefit)}</div>
            </div>
          )}
          {attentionRows.length === 0 && (
            <div className="rounded-md border border-emerald-300/25 bg-emerald-300/5 px-3 py-2 text-sm text-emerald-100">
              No benefit action due right now.
            </div>
          )}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-ink-400/60 pt-3 text-xs">
            <span className="text-slate-500">
              {rows.length} tracked benefit{rows.length === 1 ? "" : "s"}
              {missing.length ? `, ${missing.length} missing data` : ""}
            </span>
            <button className="text-cyan-accent hover:underline" onClick={() => setShowAll((value) => !value)}>
              {showAll ? "Hide full ledger" : "Show all tracked"}
            </button>
          </div>
          {showAll && standardRows.length > 0 && (
            <div>
              <div className="mb-2 flex items-center justify-between text-xs">
                <span className="font-medium uppercase tracking-wide text-slate-500">Full ledger</span>
                <span className="text-slate-500">{standardRows.length}</span>
              </div>
              <div className={benefitGridClass}>{standardRows.map(renderBenefit)}</div>
            </div>
          )}
        </div>
      )}

      {missing.length > 0 && showAll && (
        <div className="rounded-md border border-amber-300/30 bg-amber-300/5 p-3">
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="font-medium uppercase tracking-wide text-amber-100">Needs benefit data</span>
            <span className="text-slate-500">{missing.length}</span>
          </div>
          <div className={missingGridClass}>
            {missing.map((item, index) => (
              <div key={`${item.held_card_id ?? index}-${item.product_id ?? "missing"}`} className="rounded-md bg-ink-900 px-2.5 py-2">
                <div className="truncate text-xs font-medium text-slate-100">{item.display_name || cardName(item)}</div>
                <div className="mt-0.5 line-clamp-1 text-[11px] text-slate-500">{item.reason}</div>
                <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px] text-slate-500">
                  {item.verified_status && <span className={benefitSourceClass(item)}>{verifiedLabel(item)}</span>}
                  {item.last_verified && <span>{item.last_verified.slice(0, 10)}</span>}
                  {item.source_url && (
                    <a className="hover:text-cyan-accent" href={item.source_url} target="_blank" rel="noreferrer">
                      source
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

export function Profiles({ user, bump, flash }: { user: string; bump: number; flash: Flash }) {
  const [profile, setProfile] = useState<any>(null);
  const [cards, setCards] = useState<HeldCard[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [references, setReferences] = useState<CardReference[]>([]);
  const [loading, setLoading] = useState(true);
  const [balances, setBalances] = useState<{ currency: string; balance: string }[]>([]);
  const [notes, setNotes] = useState("");
  const [organicCapacity, setOrganicCapacity] = useState("");
  const [saving, setSaving] = useState(false);
  const [balanceSaving, setBalanceSaving] = useState(false);
  const [balanceDraft, setBalanceDraft] = useState({ currency: "", balance: "" });
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<HeldCard | null>(null);
  const [benefitEdits, setBenefitEdits] = useState<Record<string, BenefitEdit>>({});
  const [savingBenefit, setSavingBenefit] = useState<string | null>(null);
  const [showArchivedCards, setShowArchivedCards] = useState(false);
  const [expandedCards, setExpandedCards] = useState<Record<number, boolean>>({});

  const load = () => {
    setLoading(true);
    Promise.all([api.profile(user), api.cards(user), api.catalog(user), api.cardReferences()])
      .then(([p, c, cat, refs]) => {
        setProfile(p);
        setCards(c);
        setCatalog(cat);
        setReferences(refs);
        setBalances(Object.entries(p.point_balances ?? {}).map(([currency, balance]) => ({ currency, balance: String(balance) })));
        setNotes(p.notes ?? "");
        setOrganicCapacity(p.organic_monthly_capacity == null ? "" : String(p.organic_monthly_capacity));
        setBenefitEdits(
          Object.fromEntries(
            (p.benefit_tracker?.benefits ?? []).map((row: any) => [
              row.row_key,
              {
                amount_available: row.amount_available == null ? "" : String(row.amount_available),
                amount_used: row.amount_used == null ? "" : String(row.amount_used),
                add_amount: "",
                notes: row.notes ?? "",
              },
            ]),
          ),
        );
      })
      .catch((e) => flash("error", e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, [user, bump]);

  const currencyOptions = buildCurrencyOptions({ profile, catalog, cards, balances });

  const save = async () => {
    setSaving(true);
    try {
      const point_balances = balancesToRecord(balances);
      await api.upsertProfile(user, {
        point_balances,
        notes,
        organic_monthly_capacity: organicCapacity.trim() === "" ? null : Number(organicCapacity),
      });
      flash("info", "Profile saved.");
      load();
    } catch (e: any) {
      flash("error", e.message);
    } finally {
      setSaving(false);
    }
  };

  const persistBalances = async (nextBalances: { currency: string; balance: string }[]) => {
    setBalanceSaving(true);
    try {
      const point_balances = balancesToRecord(nextBalances);
      await api.upsertProfile(user, { point_balances, notes });
      setBalances(nextBalances);
      flash("info", "Point balance saved.");
      load();
    } catch (e: any) {
      flash("error", e.message);
    } finally {
      setBalanceSaving(false);
    }
  };

  const saveBalanceDraft = async () => {
    const currency = balanceDraft.currency.trim();
    if (!currency) {
      flash("error", "Select a currency first.");
      return;
    }
    const next = [
      ...balances.filter((b) => b.currency.trim().toLowerCase() !== currency.toLowerCase()),
      { currency, balance: String(Number(balanceDraft.balance) || 0) },
    ].sort((a, b) => a.currency.localeCompare(b.currency));
    setBalanceDraft({ currency: "", balance: "" });
    await persistBalances(next);
  };

  const editBalance = (currency: string) => {
    const row = balances.find((b) => b.currency === currency);
    setBalanceDraft({ currency, balance: row?.balance ?? "" });
  };

  const removeBalance = async (currency: string) => {
    const next = balances.filter((b) => b.currency !== currency);
    await persistBalances(next);
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
    if (!confirm(`Delete ${card.issuer} ${cardName(card)}?`)) return;
    await api.deleteCard(card.id);
    flash("info", "Card deleted.");
    load();
  };

  const onStatusCard = async (card: HeldCard, status: string) => {
    await api.updateCard(card.id, { status });
    flash("info", status === "Closed" ? "Card marked closed." : status === "Active" ? "Card reopened." : `Card marked ${status.toLowerCase()}.`);
    load();
  };

  const saveBenefitUsage = async (
    row: any,
    override: Partial<BenefitEdit> = {},
  ) => {
    const existing = benefitEdits[row.row_key] ?? {
      amount_available: row.amount_available == null ? "" : String(row.amount_available),
      amount_used: row.amount_used == null ? "" : String(row.amount_used),
      add_amount: "",
      notes: row.notes ?? "",
    };
    const edit = { ...existing, ...override };
    const binaryBenefit = isBinaryBenefit(row);
    const amountAvailable = binaryBenefit ? null : numberOrNull(edit.amount_available, row.amount_available);
    const amountUsed = binaryBenefit
      ? (override.amount_used != null ? Number(override.amount_used) : row.amount_used)
      : override.amount_used != null
        ? cappedBenefitUsed(numberOrNull(String(override.amount_used), row.amount_used), amountAvailable)
        : cappedBenefitUsed((row.amount_used ?? 0) + (numberOrNull(edit.add_amount, 0) ?? 0), amountAvailable);
    setSavingBenefit(row.row_key);
    try {
      await api.saveBenefitUsage(user, {
        held_card_id: row.held_card_id,
        benefit_key: row.benefit_key,
        benefit_name: row.benefit_name,
        period_key: row.period_key,
        period_start: row.period_start,
        period_end: row.period_end,
        amount_available: amountAvailable,
        amount_used: amountUsed,
        suppressed: edit.suppressed,
        suppress_all: Boolean(edit.suppress_all),
        notes: edit.notes.trim() || null,
      });
      flash("info", "Benefit usage saved.");
      load();
    } catch (e: any) {
      flash("error", e.message);
    } finally {
      setSavingBenefit(null);
    }
  };

  // Coverage/insurance perks live here in card details, not in the tracker.
  const protectionsByProduct = useMemo(
    () => new Map(catalog.map((row) => [row.id, row.card_protections ?? []])),
    [catalog],
  );
  const coverageFor = (c: HeldCard) =>
    c.product_id != null ? protectionsByProduct.get(c.product_id) ?? [] : [];

  if (loading && !profile) return <Spinner />;

  const f24 = profile?.five_24;
  const positiveBalances = profile?.balance_breakdown?.filter((b: any) => b.value > 0) ?? [];
  const positiveBalanceChart = positiveBalances.map((row: any, i: number) => ({
    ...row,
    pieColor: PIE_COLORS[i % PIE_COLORS.length],
  }));
  const balanceBreakdown = profile?.balance_breakdown ?? [];
  const categoryCoverage = profile?.category_coverage ?? [];
  const coveredCategories = categoryCoverage.filter((c: any) => c.covered).length;
  const benefitTracker = profile?.benefit_tracker ?? { benefits: [], missing: [], summary: {} };
  const benefitRows = benefitTracker.benefits ?? [];
  const benefitMissing = benefitTracker.missing ?? [];
  const activeProfileCards = cards.filter((card) => !isArchivedCard(card));
  const archivedProfileCards = cards.filter(isArchivedCard);
  const visibleProfileCards = showArchivedCards ? archivedProfileCards : activeProfileCards;

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

      <BenefitsTracker
        rows={benefitRows}
        missing={benefitMissing}
        summary={benefitTracker.summary ?? {}}
        edits={benefitEdits}
        savingKey={savingBenefit}
        onEdit={(rowKey, patch) =>
          setBenefitEdits((prev) => ({
            ...prev,
            [rowKey]: { ...(prev[rowKey] ?? { amount_available: "", amount_used: "", add_amount: "", notes: "" }), ...patch },
          }))
        }
        onSave={saveBenefitUsage}
      />

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
                    <div className="mt-1 text-sm font-medium text-slate-100">{cardName(item)}</div>
                    <div className="text-[11px] text-slate-500">{item.issuer}</div>
                    <div className="mt-1 text-xs text-cyan-100">
                      {categoryUseLabel(item)}
                      {item.is_fallback && <span className="ml-1 text-slate-500">from everyday</span>}
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
            {balances.length === 0 ? (
              <div className="rounded-md border border-ink-400/70 bg-ink-900 px-3 py-2 text-sm text-slate-500">
                No point balances saved yet.
              </div>
            ) : (
              <div className="grid gap-2">
                {balances.map((b) => (
                  <div key={b.currency} className="flex items-center justify-between gap-3 rounded-md border border-ink-400/70 bg-ink-900 px-3 py-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-slate-100">{b.currency}</div>
                      <div className="text-xs font-semibold tabular-nums text-cyan-accent">{fmtNum(b.balance)} pts</div>
                    </div>
                    <div className="flex shrink-0 items-center gap-3 text-xs">
                      <button className="text-slate-400 hover:text-cyan-accent" onClick={() => editBalance(b.currency)}>
                        edit
                      </button>
                      <button className="text-slate-500 hover:text-pink-accent" onClick={() => removeBalance(b.currency)} disabled={balanceSaving}>
                        remove
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div className="rounded-md border border-ink-400/70 bg-ink-800/50 p-3">
              <div className="mb-2 text-[11px] uppercase tracking-wide text-slate-500">Add / update balance</div>
              <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_130px_70px]">
                <select
                  className="input min-w-0"
                  value={balanceDraft.currency}
                  onChange={(e) => setBalanceDraft((p) => ({ ...p, currency: e.target.value }))}
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
                  value={balanceDraft.balance}
                  onChange={(e) => setBalanceDraft((p) => ({ ...p, balance: e.target.value }))}
                />
                <button className="btn-primary h-9 justify-center px-2" onClick={saveBalanceDraft} disabled={balanceSaving}>
                  {balanceSaving ? "..." : "Save"}
                </button>
              </div>
            </div>
            <div>
              <span className="label mt-2">Notes</span>
              <textarea className="input min-h-20" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </div>
            <div>
              <label className="label mt-2" htmlFor="organic-monthly-capacity">Organic monthly spend capacity (optional)</label>
              <input
                id="organic-monthly-capacity"
                className="input"
                type="number"
                min="0"
                step="1"
                placeholder="Unknown"
                value={organicCapacity}
                onChange={(e) => setOrganicCapacity(e.target.value)}
              />
              <div className="mt-1 text-[11px] text-slate-500">
                Monthly allocation before active minimum-spend commitments. Leave blank when unknown.
              </div>
            </div>
            <div className="flex justify-end">
              <button className="btn-primary w-full justify-center sm:w-auto" onClick={save} disabled={saving}>{saving ? "Saving..." : "Save profile"}</button>
            </div>
          </Card>

          {positiveBalances.length > 0 && (
            <Card className="mt-4">
              <div className="mb-2 text-[11px] uppercase tracking-wide text-slate-500">Value by currency</div>
              <div className="profile-balance-chart grid min-h-[190px] gap-3 sm:grid-cols-[190px_minmax(0,1fr)] sm:items-center">
                <div className="h-[180px] sm:h-[190px]">
                  <ResponsiveContainer>
                    <PieChart>
                      <Pie
                        data={positiveBalanceChart}
                        dataKey="value"
                        nameKey="currency"
                        innerRadius={48}
                        outerRadius={72}
                        activeShape={StablePieSector}
                        isAnimationActive={false}
                        paddingAngle={1}
                        stroke="var(--chart-stroke)"
                        strokeWidth={2}
                        rootTabIndex={-1}
                      >
                        {positiveBalanceChart.map((row: any, i: number) => (
                          <Cell key={i} className="profile-balance-sector" fill={row.pieColor} focusable="false" tabIndex={-1} />
                        ))}
                      </Pie>
                      <Tooltip content={<BalanceTooltip />} cursor={false} wrapperStyle={{ outline: "none" }} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="grid gap-1.5 text-xs">
                  {positiveBalanceChart.map((row: any) => (
                    <div key={row.currency} className="flex min-w-0 items-center justify-between gap-3 rounded-md border border-ink-400/60 bg-ink-900/70 px-2.5 py-1.5">
                      <div className="flex min-w-0 items-center gap-2">
                        <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: row.pieColor }} />
                        <span className="truncate text-slate-200">{row.currency}</span>
                      </div>
                      <span className="shrink-0 font-semibold tabular-nums text-slate-300">{fmtMoney(row.value)}</span>
                    </div>
                  ))}
                </div>
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
                      <div className="shrink-0 text-right font-semibold tabular-nums text-cyan-accent">{fmtMoney(b.value)}</div>
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
                        <td className="td tabular-nums text-slate-300">{fmtNum(b.balance)}</td>
                        <td className="td tabular-nums text-slate-400">{b.cpp ? `${b.cpp}c` : "-"}</td>
                        <td className="td font-semibold tabular-nums text-slate-200">{fmtMoney(b.value)}</td>
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
              <div className="flex flex-wrap items-center gap-2">
                <div className="flex rounded-lg border border-ink-400/70 bg-ink-900 p-1 text-xs">
                  <button
                    className={`rounded-md px-2 py-1 font-medium ${!showArchivedCards ? "bg-ink-600 text-cyan-accent" : "text-slate-400"}`}
                    onClick={() => setShowArchivedCards(false)}
                  >
                    Active {activeProfileCards.length}
                  </button>
                  <button
                    className={`rounded-md px-2 py-1 font-medium ${showArchivedCards ? "bg-ink-600 text-cyan-accent" : "text-slate-400"}`}
                    onClick={() => setShowArchivedCards(true)}
                  >
                    Closed {archivedProfileCards.length}
                  </button>
                </div>
                <button
                  className="btn-primary w-full justify-center sm:w-auto"
                  onClick={() => {
                    setEditing(null);
                    setFormOpen(true);
                  }}
                >
                  Add card
                </button>
              </div>
            }
          />

          {f24?.contributing?.length > 0 ? (
            <Card className="mb-4">
              <div className="mb-2 text-[11px] uppercase tracking-wide text-slate-500">Counting toward 5/24</div>
              <ul className="space-y-1 text-sm">
                {f24.contributing.map((c: any, i: number) => (
                  <li key={i} className="flex justify-between gap-3">
                    <span className="min-w-0 text-slate-200">{c.issuer} {cardName(c)}</span>
                    <span className="shrink-0 text-slate-500">{c.date_opened}</span>
                  </li>
                ))}
              </ul>
            </Card>
          ) : (
            <Banner kind="info">No personal-credit-reporting cards in the trailing 24 months.</Banner>
          )}

          {visibleProfileCards.length === 0 ? (
            <EmptyState
              title={showArchivedCards ? "No closed cards" : "No active cards"}
              hint={showArchivedCards ? "Closed or cancelled cards will appear here." : "Add a held card to start tracking eligibility and deadlines."}
            />
          ) : (
            <>
              <div className="soft-scroll mt-4 max-h-[56vh] space-y-1 pr-1 md:hidden">
                {visibleProfileCards.map((c) => {
                  const expanded = Boolean(expandedCards[c.id]);
                  return (
                  <Card
                    key={c.id}
                    className="cursor-pointer space-y-1 px-2.5 py-1.5 transition-colors hover:border-ink-400"
                    role="button"
                    tabIndex={0}
                    onClick={() => setExpandedCards((prev) => ({ ...prev, [c.id]: !prev[c.id] }))}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-[13px] font-medium leading-5 text-slate-100">{cardName(c)}</div>
                        <div className="text-[11px] text-slate-500">{c.issuer} - {c.ownership}</div>
                      </div>
                      <div className="flex shrink-0 items-center gap-1.5">
                        <span className="text-xs text-slate-300">{c.status}</span>
                        <span className={`text-lg text-slate-500 transition-transform ${expanded ? "rotate-90" : ""}`}>&rsaquo;</span>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                      <span>Last 4 {c.last4 ? `..${c.last4}` : "none"}</span>
                      <span>Opened {fmtDate(c.date_opened)}</span>
                      <span>Renewal {c.renewal_date ? fmtDate(c.renewal_date) : "none"}</span>
                      <span>{five24CardStatusLabel(c)}</span>
                    </div>
                    {expanded && (
                      <div className="space-y-2 border-t border-ink-500/60 pt-2">
                        <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-slate-400">
                          <span>Limit</span><span className="text-slate-300">{fmtMoney(c.credit_limit)}</span>
                          <span>Min spend</span><span className="text-slate-300">{c.min_spend_requirement ? `${fmtMoney(c.min_spend_progress ?? 0)} / ${fmtMoney(c.min_spend_requirement)}` : "none"}</span>
                          <span>Deadline</span><span className="text-slate-300">{c.min_spend_completed ? "complete" : c.min_spend_deadline ?? "none"}</span>
                          <span>Bonus</span><span className="text-slate-300">{bonusText(c)}</span>
                          <span>Credit report</span><span className="text-slate-300">{five24CardStatusLabel(c)}</span>
                        </div>
                        {c.notes && <div className="text-[11px] text-slate-500">{c.notes}</div>}
                        {coverageFor(c).length > 0 && (
                          <div className="text-[11px] text-slate-500">
                            <span className="text-slate-600">Coverage</span> {coverageFor(c).join(" · ")}
                          </div>
                        )}
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
                      </div>
                    )}
                  </Card>
                )})}
              </div>
              <div className="soft-scroll mt-4 hidden max-h-[620px] space-y-2 pr-1 md:block">
                {visibleProfileCards.map((c) => {
                  const expanded = Boolean(expandedCards[c.id]);
                  return (
                  <Card
                    key={c.id}
                    className="cursor-pointer px-4 py-3 transition-colors hover:border-ink-400"
                    role="button"
                    tabIndex={0}
                    onClick={() => setExpandedCards((prev) => ({ ...prev, [c.id]: !prev[c.id] }))}
                  >
                    <div className="flex min-w-0 items-start justify-between gap-4">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <div className="truncate text-sm font-medium text-slate-100">{cardName(c)}</div>
                          <span className="rounded-md border border-ink-400/70 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-400">
                            {c.status}
                          </span>
                        </div>
                        <div className="text-[11px] text-slate-500">{c.issuer} - {c.ownership}</div>
                        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-slate-400">
                          <span>Last 4 {c.last4 ? `..${c.last4}` : "none"}</span>
                          <span>Opened {fmtDate(c.date_opened)}</span>
                          <span>Renewal {c.renewal_date ? fmtDate(c.renewal_date) : "none"}</span>
                          <span>Limit {fmtMoney(c.credit_limit)}</span>
                          <span>{five24CardStatusLabel(c)}</span>
                        </div>
                      </div>
                      <span className={`shrink-0 text-xl text-slate-500 transition-transform ${expanded ? "rotate-90" : ""}`}>&rsaquo;</span>
                    </div>

                    {expanded && (
                      <div className="mt-3 border-t border-ink-500/60 pt-3">
                        <div className="grid gap-3 text-[11px] text-slate-400 lg:grid-cols-4">
                          <div>
                            <div className="text-slate-500">Min spend</div>
                            <div className="mt-0.5 text-slate-300">
                              {c.min_spend_requirement ? `${fmtMoney(c.min_spend_progress ?? 0)} / ${fmtMoney(c.min_spend_requirement)}` : "none"}
                            </div>
                          </div>
                          <div>
                            <div className="text-slate-500">Deadline</div>
                            <div className="mt-0.5 text-slate-300">{c.min_spend_completed ? "complete" : c.min_spend_deadline ?? "none"}</div>
                          </div>
                          <div>
                            <div className="text-slate-500">Bonus</div>
                            <div className="mt-0.5 text-slate-300">{bonusText(c)}</div>
                          </div>
                          <div>
                            <div className="text-slate-500">Credit report</div>
                            <div className="mt-0.5 text-slate-300">{five24CardStatusLabel(c)}</div>
                          </div>
                        </div>
                        {c.my_targeted_offer_points || c.notes ? (
                          <div className="mt-2 text-[11px] text-slate-500">
                            {c.my_targeted_offer_points ? <span>Targeted offer {fmtNum(c.my_targeted_offer_points)} pts. </span> : null}
                            {c.notes}
                          </div>
                        ) : null}
                        {coverageFor(c).length > 0 && (
                          <div className="mt-2 text-[11px] text-slate-500">
                            <span className="text-slate-600">Coverage</span> {coverageFor(c).join(" · ")}
                          </div>
                        )}
                        <CardActions
                          card={c}
                          className="mt-3 border-t border-ink-500/60 pt-2"
                          onEdit={() => {
                            setEditing(c);
                            setFormOpen(true);
                          }}
                          onStatus={onStatusCard}
                          onDelete={onDeleteCard}
                        />
                      </div>
                    )}
                  </Card>
                )})}
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
        references={references}
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
    <div className={`flex flex-wrap items-center justify-start gap-2 text-xs ${className}`}>
      <button
        className="rounded-md border border-ink-400/70 px-2 py-1 text-slate-400 hover:border-cyan-accent/50 hover:text-cyan-accent"
        onClick={(event) => {
          event.stopPropagation();
          onEdit();
        }}
      >
        edit
      </button>
      {!closed && !cancelPending && (
        <button
          className="rounded-md border border-ink-400/70 px-2 py-1 text-slate-400 hover:border-amber-300/50 hover:text-amber-300"
          onClick={(event) => {
            event.stopPropagation();
            onStatus(card, "Cancel Pending");
          }}
        >
          plan cancel
        </button>
      )}
      {!closed ? (
        <button
          className="rounded-md border border-ink-400/70 px-2 py-1 text-slate-400 hover:border-pink-accent/50 hover:text-pink-accent"
          onClick={(event) => {
            event.stopPropagation();
            onStatus(card, "Closed");
          }}
        >
          mark cancelled
        </button>
      ) : (
        <button
          className="rounded-md border border-ink-400/70 px-2 py-1 text-slate-400 hover:border-emerald-300/50 hover:text-emerald-300"
          onClick={(event) => {
            event.stopPropagation();
            onStatus(card, "Active");
          }}
        >
          reopen
        </button>
      )}
      <button
        className="rounded-md border border-ink-400/70 px-2 py-1 text-slate-500 hover:border-pink-accent/50 hover:text-pink-accent"
        onClick={(event) => {
          event.stopPropagation();
          onDelete(card);
        }}
      >
        delete
      </button>
    </div>
  );
}
