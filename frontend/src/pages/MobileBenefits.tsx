import { useEffect, useMemo, useState } from "react";
import { api, type BenefitRow, type ProfileSummary } from "../lib/api";
import type { Flash } from "../App";
import { Card, EmptyState, Spinner, fmtMoney } from "../components/ui";

type BenefitEdit = {
  amount_available: string;
  amount_used: string;
  add_amount: string;
  manualOpen?: boolean;
  notes: string;
  suppressed?: boolean;
  suppress_all?: boolean;
};

function cleanName(value: unknown) {
  return String(value || "Benefit")
    .replace(/\[(?:text|title|meta|json-ld|table)\]\s*/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function labelFor(row: BenefitRow) {
  return row.benefit_label || cleanName(row.benefit_name);
}

function isBinaryBenefit(row: BenefitRow) {
  return ["access", "enrollment", "membership"].includes(row.tracking_kind);
}

function valueLabel(row: BenefitRow) {
  if (row.status === "suppressed") return "Paused";
  if (isBinaryBenefit(row)) {
    if (row.status === "confirmed") return "Confirmed";
    return row.tracking_kind === "enrollment" ? "Register" : "Confirm";
  }
  if (row.amount_available != null) {
    const used = Math.min(row.amount_used ?? 0, row.amount_available);
    return `${fmtMoney(used)} / ${fmtMoney(row.amount_available)}`;
  }
  return row.display_value || "Needs data";
}

function dueLabel(row: BenefitRow) {
  if (row.status === "suppressed") return "Paused";
  if (row.days_remaining == null) return row.due_date ? row.due_date.slice(5, 10) : row.timeframe_note || "";
  if (row.days_remaining < 0) return "Expired";
  if (row.days_remaining === 0) return "Today";
  if (row.days_remaining === 1) return "1 day";
  return `${row.days_remaining} days`;
}

function remainingLabel(row: BenefitRow) {
  if (row.status === "suppressed") return "Paused by you";
  if (isBinaryBenefit(row)) {
    return row.status === "confirmed" ? "Ready to use" : "Confirm once registered or available";
  }
  if (row.amount_available != null) {
    const used = Math.min(row.amount_used ?? 0, row.amount_available);
    const remaining = Math.max(0, row.amount_available - used);
    return `${fmtMoney(remaining)} left this period`;
  }
  return row.display_value || "Amount needs data";
}

function progressWidth(row: BenefitRow) {
  if (row.status === "suppressed") return 100;
  if (isBinaryBenefit(row)) return row.status === "confirmed" ? 100 : 8;
  if (row.progress != null) return Math.min(100, Math.max(5, row.progress * 100));
  if (row.amount_available && row.amount_used != null) {
    return Math.min(100, Math.max(5, (Math.min(row.amount_used, row.amount_available) / row.amount_available) * 100));
  }
  return 8;
}

function rowPriorityClass(row: BenefitRow) {
  if (row.status === "suppressed" || row.priority === "paused") return "border-slate-500/30 bg-slate-500/10";
  if (row.priority === "attention") return "benefit-attention-row border-amber-300/40 bg-amber-300/10";
  if (row.status === "confirmed" || row.status === "used") return "border-emerald-300/30 bg-emerald-300/10";
  return "border-ink-400/60 bg-ink-900";
}

export function MobileBenefits({
  user,
  bump,
  flash,
}: {
  user: string;
  bump: number;
  flash: Flash;
}) {
  const [profile, setProfile] = useState<ProfileSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [expandedAttention, setExpandedAttention] = useState<Record<string, boolean>>({});
  const [edits, setEdits] = useState<Record<string, BenefitEdit>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    api.profile(user)
      .then((p) => {
        setProfile(p);
        setEdits(
          Object.fromEntries(
            (p.benefit_tracker?.benefits ?? []).map((row) => [
              row.row_key,
              {
                amount_available: row.amount_available == null ? "" : String(row.amount_available),
                amount_used: row.amount_used == null ? "" : String(row.amount_used),
                add_amount: "",
                notes: row.notes ?? "",
                suppressed: Boolean(row.suppressed),
              },
            ]),
          ),
        );
      })
      .catch((e) => flash("error", e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, [user, bump]);

  const rows = profile?.benefit_tracker?.benefits ?? [];
  const missing = profile?.benefit_tracker?.missing ?? [];
  const grouped = useMemo(() => {
    const groups = new Map<string, BenefitRow[]>();
    rows.forEach((row) => {
      const key = `${row.held_card_id}:${row.display_name}`;
      groups.set(key, [...(groups.get(key) ?? []), row]);
    });
    return Array.from(groups.entries()).map(([key, items]) => ({
      key,
      cardName: items[0]?.display_name || "Card",
      issuer: items[0]?.issuer || "",
      attentionCount: items.filter((row) => row.priority === "attention").length,
      rows: items.sort((a, b) => {
        const ap = a.priority === "attention" ? 0 : a.status === "suppressed" ? 2 : 1;
        const bp = b.priority === "attention" ? 0 : b.status === "suppressed" ? 2 : 1;
        return ap - bp || labelFor(a).localeCompare(labelFor(b));
      }),
    }));
  }, [rows]);

  const attentionRows = rows
    .filter((row) => row.priority === "attention" && row.status !== "suppressed")
    .sort((a, b) => (a.days_remaining ?? 999) - (b.days_remaining ?? 999))
    .slice(0, 4);

  const updateEdit = (row: BenefitRow, patch: Partial<BenefitEdit>) => {
    setEdits((prev) => ({
      ...prev,
      [row.row_key]: {
        ...(prev[row.row_key] ?? {
          amount_available: row.amount_available == null ? "" : String(row.amount_available),
          amount_used: row.amount_used == null ? "" : String(row.amount_used),
          add_amount: "",
          notes: row.notes ?? "",
        }),
        ...patch,
      },
    }));
  };

  const numberOrNull = (value: string, fallback: number | null = null) => {
    const trimmed = value.trim();
    if (trimmed === "") return fallback;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : fallback;
  };

  const capUsed = (used: number | null, available: number | null) => {
    if (used == null) return null;
    const floor = Math.max(0, used);
    return available == null ? floor : Math.min(floor, Math.max(0, available));
  };

  const saveUsage = async (row: BenefitRow, override: Partial<BenefitEdit> = {}) => {
    const existing = edits[row.row_key] ?? {
      amount_available: row.amount_available == null ? "" : String(row.amount_available),
      amount_used: row.amount_used == null ? "" : String(row.amount_used),
      add_amount: "",
      notes: row.notes ?? "",
    };
    const edit = { ...existing, ...override };
    const binary = isBinaryBenefit(row);
    const amountAvailable = binary ? null : numberOrNull(edit.amount_available, row.amount_available);
    const currentUsed = row.amount_used ?? 0;
    const amountUsed = binary
      ? (override.amount_used != null ? Number(override.amount_used) : row.amount_used)
      : override.amount_used != null
        ? capUsed(numberOrNull(String(override.amount_used), row.amount_used), amountAvailable)
        : capUsed(currentUsed + (numberOrNull(edit.add_amount, 0) ?? 0), amountAvailable);
    setSavingKey(row.row_key);
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
      setSavingKey(null);
    }
  };

  if (loading && !profile) return <Spinner />;

  const trackingControls = (row: BenefitRow) => {
    const edit = edits[row.row_key] ?? {
      amount_available: row.amount_available == null ? "" : String(row.amount_available),
      amount_used: row.amount_used == null ? "" : String(row.amount_used),
      add_amount: "",
      notes: row.notes ?? "",
      suppressed: Boolean(row.suppressed),
    };
    const binary = isBinaryBenefit(row);
    const paused = row.status === "suppressed" || edit.suppressed;
    return (
      <>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-ink-800">
          <div
            className={`h-full rounded-full ${paused ? "bg-slate-500" : "bg-cyan-accent"}`}
            style={{ width: `${progressWidth(row)}%` }}
          />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {!binary && (
            <input
              className="input h-8 max-w-[104px] text-xs"
              inputMode="decimal"
              placeholder="Add used"
              value={edit.add_amount}
              onChange={(e) => updateEdit(row, { add_amount: e.target.value })}
            />
          )}
          <button
            className="btn-primary h-8 px-3 text-xs"
            disabled={savingKey === row.row_key}
            onClick={() => saveUsage(row, binary ? { amount_used: row.status === "confirmed" ? "0" : "1" } : {})}
          >
            {savingKey === row.row_key ? "..." : binary ? (row.status === "confirmed" ? "Open" : "Confirm") : "Add"}
          </button>
          {!binary && (
            <button
              className="btn-ghost h-8 px-3 text-xs"
              type="button"
              onClick={() =>
                updateEdit(row, {
                  manualOpen: !edit.manualOpen,
                  amount_used: edit.amount_used || String(row.amount_used ?? 0),
                })
              }
            >
              Edit
            </button>
          )}
          <button
            className="btn-ghost h-8 px-3 text-xs"
            disabled={savingKey === row.row_key}
            onClick={() => saveUsage(row, { suppressed: !paused, suppress_all: !paused })}
          >
            {paused ? "Resume" : "Pause"}
          </button>
        </div>
        {!binary && edit.manualOpen && (
          <div className="mt-2 flex items-center gap-2">
            <input
              className="input h-8 max-w-[104px] text-xs"
              inputMode="decimal"
              placeholder="Set total"
              value={edit.amount_used}
              onChange={(e) => updateEdit(row, { amount_used: e.target.value })}
            />
            <button
              className="btn-ghost h-8 px-3 text-xs"
              disabled={savingKey === row.row_key}
              onClick={() => saveUsage(row, { amount_used: edit.amount_used })}
            >
              Set
            </button>
          </div>
        )}
      </>
    );
  };

  return (
    <div className="space-y-4">
      <Card className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-slate-100">Needs Attention</h2>
            <p className="text-xs text-slate-500">Expiring credits and registrations.</p>
          </div>
          <span className="rounded-full border border-ink-400/60 px-2 py-0.5 text-xs text-slate-400">
            {attentionRows.length}
          </span>
        </div>
        {attentionRows.length === 0 ? (
          <div className="rounded-lg border border-ink-400/60 bg-ink-900 px-3 py-2 text-sm text-slate-400">
            Nothing urgent right now.
          </div>
        ) : (
          <div className="space-y-2">
            {attentionRows.map((row) => {
              const isOpen = Boolean(expandedAttention[row.row_key]);
              return (
              <div key={`attention-${row.row_key}`} className="benefit-attention-row rounded-lg border border-amber-300/40 bg-amber-300/10 px-3 py-2">
                <button
                  className="flex w-full items-start justify-between gap-3 text-left"
                  onClick={() => setExpandedAttention((prev) => ({ ...prev, [row.row_key]: !prev[row.row_key] }))}
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-slate-100">{labelFor(row)}</div>
                    <div className="truncate text-xs text-slate-500">{row.display_name}</div>
                  </div>
                  <div className="flex shrink-0 items-start gap-2 text-right text-xs text-amber-100">
                    <span>{dueLabel(row)}</span>
                    <span className={`text-lg leading-none text-slate-500 transition-transform ${isOpen ? "rotate-90" : ""}`}>&rsaquo;</span>
                  </div>
                </button>
                {isOpen && (
                  <>
                    <div className="mt-2 rounded-md border border-ink-400/50 bg-ink-900/60 px-2.5 py-2 text-xs">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-slate-500">Track</span>
                        <span className="font-semibold text-slate-100">{valueLabel(row)}</span>
                      </div>
                      <div className="mt-1 flex items-center justify-between gap-3">
                        <span className="text-slate-500">Left</span>
                        <span className="text-slate-300">{remainingLabel(row)}</span>
                      </div>
                      {(row.timeframe_note || row.cadence || row.due_date) && (
                        <div className="mt-1 flex items-center justify-between gap-3">
                          <span className="text-slate-500">Window</span>
                          <span className="text-right text-slate-300">{row.timeframe_note || row.cadence || row.due_date}</span>
                        </div>
                      )}
                    </div>
                    {trackingControls(row)}
                  </>
                )}
              </div>
            )})}
          </div>
        )}
      </Card>

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-100">{user}'s Benefits</h2>
          <span className="text-xs text-slate-500">{grouped.length} cards</span>
        </div>
        {grouped.length === 0 ? (
          <EmptyState title="No benefits loaded yet" hint="Run Deep Refresh for active cards, then review proposed changes." />
        ) : (
          <div className="space-y-2">
            {grouped.map((group) => {
              const isOpen = Boolean(expanded[group.key]);
              return (
                <Card key={group.key} className="p-0">
                  <button
                    className="flex w-full items-center justify-between gap-3 px-3 py-3 text-left"
                    onClick={() => setExpanded((prev) => ({ ...prev, [group.key]: !prev[group.key] }))}
                  >
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-slate-100">{group.cardName}</div>
                      <div className="text-xs text-slate-500">
                        {group.rows.length} benefit{group.rows.length === 1 ? "" : "s"}
                        {group.attentionCount ? ` | ${group.attentionCount} due` : ""}
                      </div>
                    </div>
                    <span className={`shrink-0 text-lg text-slate-400 transition-transform ${isOpen ? "rotate-90" : ""}`}>
                      &rsaquo;
                    </span>
                  </button>
                  {isOpen && (
                    <div className="space-y-2 border-t border-ink-400/60 p-3">
                      {group.rows.map((row) => {
                        return (
                          <div key={row.row_key} className={`rounded-lg border px-3 py-3 ${rowPriorityClass(row)}`}>
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <div className="line-clamp-2 text-sm font-semibold text-slate-100">{labelFor(row)}</div>
                                <div className="mt-0.5 text-xs text-slate-500">{row.timeframe_note || row.cadence}</div>
                              </div>
                              <div className="shrink-0 text-right">
                                <div className="text-xs text-slate-400">{dueLabel(row)}</div>
                                <div className="text-xs font-semibold text-cyan-accent">{valueLabel(row)}</div>
                              </div>
                            </div>
                            {trackingControls(row)}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </section>

      {missing.length > 0 && (
        <Card>
          <div className="text-sm font-semibold text-amber-100">Needs Benefit Data</div>
          <div className="mt-2 space-y-1 text-xs text-slate-400">
            {missing.slice(0, 4).map((item) => (
              <div key={`${item.held_card_id}-${item.product_name}`}>{item.display_name || item.product_name}</div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
