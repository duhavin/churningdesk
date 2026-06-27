import { useEffect, useMemo, useState } from "react";
import { Field, Modal, cardName } from "./ui";
import type { CardReference, CatalogEntry, HeldCard } from "../lib/api";

const OWNERSHIP = ["Personal", "Business"];
const ACCOUNT_TYPES = ["Credit Card", "Charge Card", "Flexible Spending Credit Card"];
const STATUSES = ["Active", "Downgrade Pending", "Cancel Pending", "Closed"];

export function CardForm({
  open,
  onClose,
  onSubmit,
  user,
  catalog,
  references = [],
  initial,
  prefill,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (payload: any) => Promise<void>;
  user: string;
  catalog: CatalogEntry[];
  references?: CardReference[];
  initial?: HeldCard | null;
  prefill?: Record<string, any> | null;
}) {
  const blank = {
    user,
    issuer: "",
    product_name: "",
    product_id: null as number | null,
    last4: "",
    date_opened: "",
    ownership: "Personal",
    account_type: "Credit Card",
    reports_to_personal_credit: true,
    credit_limit: "",
    annual_fee: "",
    renewal_date: "",
    welcome_bonus_earned: false,
    bonus_points_earned: "",
    bonus_currency: "",
    bonus_earned_date: "",
    min_spend_requirement: "",
    min_spend_deadline: "",
    min_spend_progress: "",
    min_spend_completed: false,
    my_targeted_offer_points: "",
    my_targeted_offer_notes: "",
    status: "Active",
    notes: "",
  };

  const [f, setF] = useState<any>(initial ? { ...blank, ...initial } : prefill ? { ...blank, ...prefill } : blank);
  const [quickQuery, setQuickQuery] = useState("");
  const [quickOpen, setQuickOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Resync when (re)opened for a different card / prefill.
  useEffect(() => {
    if (!open) return;
    setF(initial ? { ...blank, ...initial } : prefill ? { ...blank, ...prefill } : blank);
    setQuickQuery("");
    setQuickOpen(false);
    setErr(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initial, prefill]);

  const set = (k: string, v: any) => setF((p: any) => ({ ...p, [k]: v }));
  const catalogOption = (c: Pick<CatalogEntry, "issuer" | "product_name" | "display_name">) => `${c.issuer} - ${cardName(c)}`;

  const optionRows = useMemo(() => {
    const catalogByKey = new Map(catalog.map((c) => [c.canonical_key, c]));
    return [
      ...catalog.map((c) => ({
        identity: c.canonical_key || `catalog-${c.id}`,
        value: catalogOption(c),
        label: cardName(c),
        sublabel: [c.issuer, c.product_name, c.ownership, c.currency].filter(Boolean).join(" | "),
        searchText: [c.issuer, cardName(c), c.product_name, c.currency, c.ownership].filter(Boolean).join(" "),
        catalog: c,
        reference: null as CardReference | null,
      })),
      ...references
        .filter((r) => r.active !== false)
        .map((r) => {
          const catalogMatch = r.canonical_key ? catalogByKey.get(r.canonical_key) : undefined;
          return {
            identity: r.canonical_key || `reference-${r.id}`,
            value: catalogOption(r),
            label: cardName(r),
            sublabel: [r.issuer, r.product_name, r.ownership, r.currency, "reference"].filter(Boolean).join(" | "),
            searchText: [
              r.issuer,
              cardName(r),
              r.product_name,
              r.currency,
              r.ownership,
              ...(r.aliases ?? []),
              ...(r.search_terms ?? []),
            ]
              .filter(Boolean)
              .join(" "),
            catalog: catalogMatch ?? null,
            reference: r,
          };
        }),
    ].filter((row, index, rows) => rows.findIndex((other) => other.identity === row.identity) === index);
  }, [catalog, references]);

  const quickResults = useMemo(() => {
    const tokens = quickQuery
      .toLowerCase()
      .split(/[^a-z0-9]+/)
      .filter(Boolean);
    const rows = optionRows
      .map((row) => {
        const haystack = row.searchText.toLowerCase();
        const exact = row.label.toLowerCase() === quickQuery.trim().toLowerCase() ? 50 : 0;
        const starts = row.label.toLowerCase().startsWith(quickQuery.trim().toLowerCase()) ? 20 : 0;
        const tokenScore = tokens.reduce((sum, token) => sum + (haystack.includes(token) ? 10 : -100), 0);
        return { row, score: exact + starts + tokenScore };
      })
      .filter((item) => tokens.length === 0 || item.score >= tokens.length * 8)
      .sort((a, b) => b.score - a.score || a.row.label.localeCompare(b.row.label))
      .map((item) => item.row);
    return rows.slice(0, 12);
  }, [optionRows, quickQuery]);

  const applyOption = (row: (typeof optionRows)[number]) => {
    const match = row?.catalog ?? null;
    const reference = row?.reference ?? null;
    const source = match ?? reference;
    if (source) {
      setF((p: any) => ({
        ...p,
        issuer: source.issuer,
        product_name: source.product_name,
        product_id: match?.id ?? null,
        ownership: source.ownership ?? p.ownership,
        account_type: source.account_type ?? p.account_type,
        reports_to_personal_credit: source.reports_to_personal_credit ?? p.reports_to_personal_credit,
        annual_fee: match?.annual_fee ?? p.annual_fee,
        min_spend_requirement: match?.current_offer_min_spend ?? p.min_spend_requirement,
      }));
      setQuickQuery(row.value);
      setQuickOpen(false);
    }
  };

  const submit = async () => {
    setErr(null);
    if (!f.issuer || !f.product_name || !f.date_opened) {
      setErr("Issuer, product name, and date opened are required.");
      return;
    }
    setBusy(true);
    try {
      const num = (v: any) => (v === "" || v === null ? null : Number(v));
      await onSubmit({
        user,
        issuer: f.issuer,
        product_name: f.product_name,
        product_id: f.product_id,
        last4: f.last4 || null,
        date_opened: f.date_opened,
        ownership: f.ownership,
        account_type: f.account_type,
        reports_to_personal_credit: f.reports_to_personal_credit,
        credit_limit: num(f.credit_limit),
        annual_fee: num(f.annual_fee),
        renewal_date: f.renewal_date || null,
        welcome_bonus_earned: f.welcome_bonus_earned,
        bonus_points_earned: num(f.bonus_points_earned),
        bonus_currency: f.bonus_currency || null,
        bonus_earned_date: f.bonus_earned_date || null,
        min_spend_requirement: num(f.min_spend_requirement),
        min_spend_deadline: f.min_spend_deadline || null,
        min_spend_progress: num(f.min_spend_progress),
        min_spend_completed: f.min_spend_completed,
        my_targeted_offer_points: num(f.my_targeted_offer_points),
        my_targeted_offer_notes: f.my_targeted_offer_notes || null,
        status: f.status,
        notes: f.notes || null,
      });
      onClose();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={initial ? "Edit held card" : "Add held card"} wide>
      {err && <div className="mb-3 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{err}</div>}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="col-span-full">
          <Field label="Quick-fill from catalog (optional)">
            <div className="relative">
              <input
                className="input"
                value={quickQuery}
                placeholder="Search Amex, Bilt, Sapphire, Venture..."
                onFocus={() => setQuickOpen(true)}
                onChange={(e) => {
                  setQuickQuery(e.target.value);
                  setQuickOpen(true);
                }}
              />
              {quickOpen && quickResults.length > 0 && (
                <div className="absolute z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-ink-400/70 bg-ink-800 p-1 shadow-xl">
                  {quickResults.map((row) => (
                    <button
                      key={row.catalog ? `catalog-${row.catalog.id}` : `reference-${row.reference?.id}`}
                      type="button"
                      className="settings-menu-item w-full rounded-md px-2 py-2 text-left"
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => applyOption(row)}
                    >
                      <div className="text-sm font-medium text-slate-100">{row.label}</div>
                      <div className="text-[11px] text-slate-500">{row.sublabel}</div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Field>
        </div>

        <Field label="Issuer *">
          <input className="input" value={f.issuer} onChange={(e) => set("issuer", e.target.value)} />
        </Field>
        <Field label="Product name *">
          <input className="input" value={f.product_name} onChange={(e) => set("product_name", e.target.value)} />
        </Field>
        <Field label="Date opened *  (account approval date — drives 5/24 & eligibility)">
          <input type="date" className="input" value={f.date_opened || ""} onChange={(e) => set("date_opened", e.target.value)} />
          <span className="mt-0.5 block text-[10px] text-slate-500">When this card account was opened/approved.</span>
        </Field>
        <Field label="Last 4  🔒">
          <input className="input" maxLength={4} value={f.last4 || ""} onChange={(e) => set("last4", e.target.value)} />
        </Field>
        <Field label="Ownership">
          <select className="input" value={f.ownership} onChange={(e) => set("ownership", e.target.value)}>
            {OWNERSHIP.map((o) => <option key={o}>{o}</option>)}
          </select>
        </Field>
        <Field label="Account type">
          <select className="input" value={f.account_type} onChange={(e) => set("account_type", e.target.value)}>
            {ACCOUNT_TYPES.map((o) => <option key={o}>{o}</option>)}
          </select>
        </Field>
        <Field label="Credit limit  🔒">
          <input type="number" className="input" value={f.credit_limit} onChange={(e) => set("credit_limit", e.target.value)} />
        </Field>
        <Field label="Annual fee ($)">
          <input type="number" className="input" value={f.annual_fee} onChange={(e) => set("annual_fee", e.target.value)} />
        </Field>
        <Field label="Annual-fee renewal date">
          <input type="date" className="input" value={f.renewal_date || ""} onChange={(e) => set("renewal_date", e.target.value)} />
          <span className="mt-0.5 block text-[10px] text-slate-500">Next date the annual fee posts (cardmember anniversary).</span>
        </Field>
        <Field label="Status">
          <select className="input" value={f.status} onChange={(e) => set("status", e.target.value)}>
            {STATUSES.map((o) => <option key={o}>{o}</option>)}
          </select>
        </Field>

        <div className="col-span-full mt-1 border-t border-ink-400/50 pt-2 text-[11px] uppercase tracking-wide text-slate-500">
          Bonus
        </div>
        <label className="col-span-full flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={f.welcome_bonus_earned} onChange={(e) => set("welcome_bonus_earned", e.target.checked)} />
          Welcome bonus earned
        </label>
        <Field label="Bonus points earned">
          <input type="number" className="input" value={f.bonus_points_earned} onChange={(e) => set("bonus_points_earned", e.target.value)} />
        </Field>
        <Field label="Bonus currency">
          <input className="input" value={f.bonus_currency || ""} onChange={(e) => set("bonus_currency", e.target.value)} />
        </Field>
        <Field label="Welcome-bonus earned date">
          <input type="date" className="input" value={f.bonus_earned_date || ""} onChange={(e) => set("bonus_earned_date", e.target.value)} />
          <span className="mt-0.5 block text-[10px] text-slate-500">When the sign-up bonus posted (drives Sapphire 48-mo / re-eligibility).</span>
        </Field>
        <label className="flex items-center gap-2 text-sm text-slate-300 mt-6">
          <input
            type="checkbox"
            checked={f.reports_to_personal_credit}
            onChange={(e) => set("reports_to_personal_credit", e.target.checked)}
          />
          Reports to personal credit (counts toward 5/24)
        </label>
        <div className="col-span-full mt-1 border-t border-ink-400/50 pt-2 text-[11px] uppercase tracking-wide text-slate-500">
          Minimum spend
        </div>
        <Field label="Requirement ($)">
          <input type="number" className="input" value={f.min_spend_requirement ?? ""} onChange={(e) => set("min_spend_requirement", e.target.value)} />
        </Field>
        <Field label="Progress ($)">
          <input type="number" className="input" value={f.min_spend_progress ?? ""} onChange={(e) => set("min_spend_progress", e.target.value)} />
        </Field>
        <Field label="Deadline">
          <input type="date" className="input" value={f.min_spend_deadline || ""} onChange={(e) => set("min_spend_deadline", e.target.value)} />
        </Field>
        <label className="flex items-center gap-2 text-sm text-slate-300 mt-6">
          <input type="checkbox" checked={f.min_spend_completed} onChange={(e) => set("min_spend_completed", e.target.checked)} />
          Minimum spend complete
        </label>

        <div className="col-span-full mt-1 border-t border-ink-400/50 pt-2 text-[11px] uppercase tracking-wide text-slate-500">
          My targeted / referral offer (private, optional)
        </div>
        <Field label="Targeted offer points  🔒">
          <input type="number" className="input" value={f.my_targeted_offer_points} onChange={(e) => set("my_targeted_offer_points", e.target.value)} />
        </Field>
        <Field label="Targeted offer notes">
          <input className="input" value={f.my_targeted_offer_notes || ""} onChange={(e) => set("my_targeted_offer_notes", e.target.value)} />
        </Field>

        <div className="col-span-full">
          <Field label="Notes">
            <textarea className="input" rows={2} value={f.notes || ""} onChange={(e) => set("notes", e.target.value)} />
          </Field>
        </div>
      </div>

      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" onClick={submit} disabled={busy}>
          {busy ? "Saving…" : initial ? "Save changes" : "Add card"}
        </button>
      </div>
    </Modal>
  );
}
