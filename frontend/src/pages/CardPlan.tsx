import { Fragment, useEffect, useMemo, useState } from "react";
import { api, type CatalogEntry } from "../lib/api";
import type { Flash } from "../App";
import {
  Banner,
  Card,
  EmptyState,
  Field,
  Modal,
  ScoreBar,
  SectionTitle,
  Spinner,
  StatusBadge,
  fmtMoney,
  fmtNum,
} from "../components/ui";

const STATUS_ORDER = ["APPLY NOW", "WATCH", "WAIT", "NEEDS DATA", "LOW PRIORITY", "FUTURE", "SKIP"];

function compactEntries(obj: Record<string, any> | null | undefined) {
  if (!obj) return "";
  return Object.entries(obj)
    .slice(0, 3)
    .map(([k, v]) => `${k} ${String(v)}`)
    .join(" · ");
}

function compactList(items: any[] | null | undefined) {
  if (!items?.length) return "";
  return items
    .slice(0, 3)
    .map((item) => (typeof item === "string" ? item : item?.name ?? item?.title ?? JSON.stringify(item)))
    .join(", ");
}

function detailEntries(obj: Record<string, any> | null | undefined) {
  if (!obj || Object.keys(obj).length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {Object.entries(obj).map(([k, v]) => (
        <span key={k} className="rounded-md border border-ink-400/70 bg-ink-900 px-2 py-1 text-xs text-slate-300">
          <span className="text-slate-500">{k}:</span> {String(v)}
        </span>
      ))}
    </div>
  );
}

function detailList(items: any[] | null | undefined) {
  if (!items?.length) return null;
  return (
    <ul className="space-y-1 text-xs text-slate-300">
      {items.map((item, i) => (
        <li key={i}>{typeof item === "string" ? item : item?.name ?? item?.title ?? JSON.stringify(item)}</li>
      ))}
    </ul>
  );
}

function offerLine(r: CatalogEntry) {
  const parts: string[] = [];
  if (r.current_offer_effective) parts.push(`${fmtNum(r.current_offer_effective)} pts`);
  if (r.current_offer_cash) parts.push(`${fmtMoney(r.current_offer_cash)} cash`);
  if (!r.current_offer_effective && r.current_offer_cash) return `${fmtMoney(r.current_offer_cash)} cash`;
  if (parts.length) return parts.join(" + ");
  if (r.last_verified && !r.needs_data) return "No bonus";
  return "Needs data";
}

function cashLine(r: CatalogEntry) {
  if (r.current_offer_cash && !r.current_offer_effective) return "";
  if (r.current_offer_cash) return `Cash ${fmtMoney(r.current_offer_cash)}`;
  return "";
}

function spendLine(r: CatalogEntry) {
  if (!r.current_offer_min_spend) return "";
  const window = r.current_offer_window_months ? ` / ${r.current_offer_window_months}mo` : "";
  return `${fmtMoney(r.current_offer_min_spend)}${window}`;
}

function referralLine(r: CatalogEntry) {
  const parts: string[] = [];
  if (r.referral_bonus_effective) parts.push(`${fmtNum(r.referral_bonus_effective)} pts`);
  if (r.referral_bonus_cash) parts.push(`${fmtMoney(r.referral_bonus_cash)} cash`);
  return parts.length ? `Ref ${parts.join(" + ")}` : "";
}

function mobileSummaryLine(r: CatalogEntry) {
  const peak = fmtNum(r.peak_offer_effective ?? r.peak_offer_points);
  return [offerLine(r), spendLine(r), peak ? `Peak ${peak}` : "", fmtMoney(r.offer_value)].filter(Boolean).join(" | ");
}

function shortVerified(value: string | null | undefined) {
  if (!value) return "";
  const parsed = value.includes("T") ? new Date(value) : new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(parsed);
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
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [issuer, setIssuer] = useState("");
  const [status, setStatus] = useState("");
  const [tag, setTag] = useState("");
  const [sort, setSort] = useState("smart");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<CatalogEntry | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const load = () => {
    setLoading(true);
    api
      .catalog(user)
      .then(setRows)
      .catch((e) => flash("error", e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, [user, bump]);

  const issuers = useMemo(() => [...new Set(rows.map((r) => r.issuer))].sort(), [rows]);
  const tags = useMemo(() => [...new Set(rows.map((r) => r.tag).filter(Boolean))] as string[], [rows]);

  const filtered = useMemo(() => {
    let r = rows.filter((x) => {
      if (issuer && x.issuer !== issuer) return false;
      if (status && x.status !== status) return false;
      if (tag && x.tag !== tag) return false;
      if (q) {
        const s = `${x.issuer} ${x.product_name} ${x.currency ?? ""}`.toLowerCase();
        if (!s.includes(q.toLowerCase())) return false;
      }
      return true;
    });
    if (sort === "peak_score") r = [...r].sort((a, b) => b.peak_score - a.peak_score);
    else if (sort === "value") r = [...r].sort((a, b) => b.offer_value - a.offer_value);
    else
      r = [...r].sort((a, b) => {
        const sa = STATUS_ORDER.indexOf(a.status);
        const sb = STATUS_ORDER.indexOf(b.status);
        if (sa !== sb) return sa - sb;
        return b.offer_value - a.offer_value;
      });
    return r;
  }, [rows, issuer, status, tag, q, sort]);

  const onSave = async (payload: any) => {
    if (editing) await api.updateProduct(editing.id, payload);
    else await api.createProduct(payload);
    flash("info", editing ? "Product updated." : "Product added to catalog.");
    setFormOpen(false);
    load();
  };

  const onDelete = async (r: CatalogEntry) => {
    if (!confirm(`Delete ${r.issuer} ${r.product_name} from the catalog?`)) return;
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
      flash("info", v == null ? "Targeted offer cleared." : `Targeted offer set for ${r.product_name}.`);
      load();
    } catch (e: any) {
      flash("error", e.message);
    }
  };

  if (loading && rows.length === 0) return <Spinner />;

  return (
    <div className="space-y-4">
      <SectionTitle
        title="Card Plan"
        subtitle={`Effective catalog scored for ${user} — current offer vs all-time peak (target), value, and rank.`}
        right={
          <button
            className="btn-primary"
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
          >
            + Add product
          </button>
        }
      />

      {rows.length === 0 ? (
        <EmptyState
          title="Catalog is empty"
          hint="No seed data by design. Use the Run menu → Discover cards to enumerate the universe, then Refresh offers to scrape current/peak offers."
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
            <input className="input col-span-2 sm:max-w-xs" placeholder="Search..." value={q} onChange={(e) => setQ(e.target.value)} />
            <select className="input sm:max-w-[160px]" value={issuer} onChange={(e) => setIssuer(e.target.value)}>
              <option value="">All issuers</option>
              {issuers.map((i) => <option key={i}>{i}</option>)}
            </select>
            <select className="input sm:max-w-[150px]" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              {STATUS_ORDER.map((s) => <option key={s}>{s}</option>)}
            </select>
            <select className="input sm:max-w-[150px]" value={tag} onChange={(e) => setTag(e.target.value)}>
              <option value="">All tags</option>
              {tags.map((t) => <option key={t}>{t}</option>)}
            </select>
            <select className="input sm:ml-auto sm:max-w-[170px]" value={sort} onChange={(e) => setSort(e.target.value)}>
              <option value="smart">Sort: status, then value</option>
              <option value="peak_score">Sort: peak score</option>
              <option value="value">Sort: offer value</option>
            </select>
          </div>

          <div className="space-y-2 md:hidden">
            {filtered.map((r) => (
              <Card
                key={r.id}
                className="cursor-pointer space-y-2 px-3 py-2"
                onClick={() => toggleExpanded(r.id)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-slate-100">{r.product_name}</div>
                    <div className="text-[11px] text-slate-500">
                      {r.issuer} · {r.ownership}{r.currency ? ` · ${r.currency}` : ""}
                    </div>
                  </div>
                  <div className="shrink-0">
                    <StatusBadge status={r.status} />
                  </div>
                </div>

                <div className="text-xs leading-snug text-slate-300">{mobileSummaryLine(r)}</div>

                <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                  {r.targeted_beats_public && (
                    <span className="chip bg-pink-accent/15 text-pink-accent">targeted high</span>
                  )}
                  {referralLine(r) && <span className="chip bg-cyan-accent/10 text-cyan-100">{referralLine(r)}</span>}
                  {compactEntries(r.best_category_uses) && (
                    <span className="min-w-0 truncate text-[11px] text-slate-500">Use: {compactEntries(r.best_category_uses)}</span>
                  )}
                </div>

                {expanded.has(r.id) && (
                  <div className="space-y-3 border-t border-ink-400/50 pt-3">
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <PhoneMetric label="Now" value={offerLine(r)} />
                      <PhoneMetric label="Peak" value={fmtNum(r.peak_offer_effective ?? r.peak_offer_points)} />
                      <PhoneMetric label="Spend" value={spendLine(r) || "none"} />
                      <PhoneMetric label="Value" value={fmtMoney(r.offer_value)} />
                    </div>
                    <div onClick={(e) => e.stopPropagation()}>
                      <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">Targeted</div>
                      <ManualOfferCell
                        value={r.manual_targeted_offer?.offer_points ?? null}
                        expiresAt={r.manual_targeted_offer?.expires_at ?? null}
                        onSave={(v, expiresAt) => saveManualOffer(r, v, expiresAt)}
                      />
                    </div>
                    <div className="grid gap-3 text-xs">
                      <div>
                        <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">Use</div>
                        {detailEntries(r.earn_multipliers) ?? detailEntries(r.best_category_uses) ?? (
                          <div className="text-slate-500">No earn/category data yet.</div>
                        )}
                      </div>
                      <div>
                        <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">Benefits</div>
                        {detailList(r.card_benefits) ?? <div className="text-slate-500">No benefits captured yet.</div>}
                      </div>
                      {detailList(r.downgrade_paths) && (
                        <div>
                          <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">Downgrade</div>
                          {detailList(r.downgrade_paths)}
                        </div>
                      )}
                      <div className="flex items-center justify-between gap-3 text-[11px] text-slate-500">
                        <span>AF {fmtMoney(r.annual_fee)}</span>
                        <span>{r.last_verified ? `Verified ${shortVerified(r.last_verified)}` : "Not verified"}</span>
                      </div>
                    </div>
                  </div>
                )}
              </Card>
            ))}
          </div>

          <Card className="hidden overflow-x-auto p-0 md:block">
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">#</th>
                  <th className="th">Card</th>
                  <th className="th">Current</th>
                  <th className="th">Targeted offer</th>
                  <th className="th">Peak (target)</th>
                  <th className="th">Peak score</th>
                  <th className="th">Value</th>
                  <th className="th">Status</th>
                  <th className="th">Eligibility</th>
                  <th className="th"></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <Fragment key={r.id}>
                  <tr className="cursor-pointer hover:bg-ink-600/40" onClick={() => toggleExpanded(r.id)}>
                    <td className="td text-slate-500 font-mono text-xs">{r.rank ?? "—"}</td>
                    <td className="td">
                      <div className="font-medium text-slate-100">{r.product_name}</div>
                      <div className="text-[11px] text-slate-500">
                        {r.issuer} · {r.ownership}
                        {r.currency ? ` · ${r.currency}` : ""}
                        {r.tag ? ` · ${r.tag}` : ""}
                        {["llm_discovery", "static_discovery"].includes(r.added_by) && !r.discovery_reviewed ? " · new" : ""}
                      </div>
                      <div className="mt-1 space-y-0.5 text-[11px] text-slate-400">
                        <div>{offerLine(r)}</div>
                        {cashLine(r) && <div>{cashLine(r)}</div>}
                        {spendLine(r) && <div>{spendLine(r)}</div>}
                        {referralLine(r) && <div>{referralLine(r)}</div>}
                      </div>
                      {compactEntries(r.best_category_uses) && (
                        <div className="mt-1 text-[11px] text-slate-400">Use: {compactEntries(r.best_category_uses)}</div>
                      )}
                      {compactList(r.card_benefits) && (
                        <div className="mt-0.5 text-[11px] text-slate-500">Benefits: {compactList(r.card_benefits)}</div>
                      )}
                      {compactList(r.downgrade_paths) && (
                        <div className="mt-0.5 text-[11px] text-slate-500">Downgrade: {compactList(r.downgrade_paths)}</div>
                      )}
                      {shortVerified(r.last_verified) && (
                        <div className="mt-0.5 text-[11px] text-slate-600">Verified {shortVerified(r.last_verified)}</div>
                      )}
                    </td>
                    <td className="td">
                      <span className="font-mono text-slate-200">
                        {r.current_offer_effective ? fmtNum(r.current_offer_effective) : r.current_offer_cash ? fmtMoney(r.current_offer_cash) : "needs data"}
                      </span>
                      {r.targeted_beats_public && (
                        <span className="ml-1 chip bg-pink-accent/15 text-pink-accent" title="Your targeted offer beats the public offer">
                          targeted ▲
                        </span>
                      )}
                    </td>
                    <td className="td">
                      <ManualOfferCell
                        value={r.manual_targeted_offer?.offer_points ?? null}
                        expiresAt={r.manual_targeted_offer?.expires_at ?? null}
                        onSave={(v, expiresAt) => saveManualOffer(r, v, expiresAt)}
                      />
                    </td>
                    <td className="td font-mono text-slate-400">
                      {fmtNum(r.peak_offer_effective ?? r.peak_offer_points)}
                      {r.targeted_peak_offer_points && r.targeted_peak_offer_points > (r.peak_offer_points ?? 0) && (
                        <div className="text-[11px] text-pink-accent">targeted high</div>
                      )}
                      {r.needs_data && <div className="text-[11px] text-amber-300">needs data</div>}
                    </td>
                    <td className="td"><ScoreBar score={r.peak_score} /></td>
                    <td className="td font-mono text-slate-200">{fmtMoney(r.offer_value)}</td>
                    <td className="td"><StatusBadge status={r.status} /></td>
                    <td className="td">
                      {r.eligibility.eligible ? (
                        <span className="text-emerald-300/90 text-xs">eligible</span>
                      ) : (
                        <span
                          className="text-rose-300/90 text-xs cursor-help underline decoration-dotted"
                          title={
                            r.eligibility.reasons.join(" · ") +
                            (r.eligibility.earliest_eligible_date
                              ? ` (earliest ${r.eligibility.earliest_eligible_date})`
                              : "")
                          }
                        >
                          {r.eligibility.block_type === "permanent" ? "blocked" : "wait"}
                          {r.eligibility.earliest_eligible_date ? ` → ${r.eligibility.earliest_eligible_date}` : ""}
                        </span>
                      )}
                    </td>
                    <td className="td whitespace-nowrap text-right">
                      <button
                        className="text-xs text-slate-400 hover:text-cyan-accent"
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditing(r);
                          setFormOpen(true);
                        }}
                      >
                        edit
                      </button>
                      <button className="ml-3 text-xs text-slate-400 hover:text-pink-accent" onClick={(e) => { e.stopPropagation(); onDelete(r); }}>
                        del
                      </button>
                    </td>
                  </tr>
                  {expanded.has(r.id) && (
                    <tr className="cursor-pointer bg-ink-900/45" onClick={() => toggleExpanded(r.id)}>
                      <td className="td"></td>
                      <td className="td" colSpan={9}>
                        <div className="grid gap-4 py-2 lg:grid-cols-3">
                          <div>
                            <div className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">Offer</div>
                            <div className="space-y-1 text-xs text-slate-300">
                              <div>AF <span className="font-mono">{fmtMoney(r.annual_fee)}</span></div>
                              <div>Now <span className="font-mono">{offerLine(r)}</span></div>
                              <div>Peak: <span className="font-mono">{fmtNum(r.peak_offer_effective ?? r.peak_offer_points)} {r.currency ?? "pts"}</span></div>
                              <div>Ref <span className="font-mono">{referralLine(r).replace(/^Ref /, "") || "none"}</span></div>
                              <div>Credits <span className="font-mono">{fmtMoney(r.first_year_credit_value)}</span></div>
                              <div>Personal report: {r.reports_to_personal_credit ? "yes" : "no"}</div>
                            </div>
                          </div>
                          <div>
                            <div className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">Use</div>
                            {detailEntries(r.earn_multipliers) ?? detailEntries(r.best_category_uses) ?? (
                              <div className="text-xs text-slate-500">No earn/category data yet.</div>
                            )}
                          </div>
                          <div>
                            <div className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">Benefits</div>
                            <div className="space-y-3">
                              {detailList(r.card_benefits) ?? <div className="text-xs text-slate-500">No benefits captured yet.</div>}
                              {detailList(r.downgrade_paths) && (
                                <div>
                                  <div className="mb-1 text-[11px] text-slate-500">Downgrade</div>
                                  {detailList(r.downgrade_paths)}
                                </div>
                              )}
                              {r.eligibility_tags?.length ? (
                                <div className="flex flex-wrap gap-1">
                                  {r.eligibility_tags.map((tag) => (
                                    <span key={tag} className="chip bg-ink-700 text-slate-300">{tag}</span>
                                  ))}
                                </div>
                              ) : null}
                              <div className="text-[11px] text-slate-500">
                                {r.source_url ? <a href={r.source_url} target="_blank" rel="noreferrer" className="text-cyan-accent hover:underline">source</a> : "No source URL"}
                                {r.last_verified ? ` · verified ${r.last_verified}` : ""}
                              </div>
                            </div>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </Card>
          <p className="text-[11px] text-slate-500">
            Targeted offers entered here are private per user. Public offers, peaks, benefits, and referral bonuses remain catalog data.
          </p>
        </>
      )}

      <ProductForm open={formOpen} onClose={() => setFormOpen(false)} onSave={onSave} initial={editing} />
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
    <div className="flex min-w-0 flex-wrap items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
      <input
        type="text"
        inputMode="numeric"
        className="input max-w-[118px] flex-1 py-1 text-xs"
        placeholder="100000"
        title="Private targeted offer points for this user"
        value={v}
        onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
      />
      <button className="btn-ghost px-2 py-1 text-xs" onClick={commit}>Save</button>
      {expiresAt && <span className="basis-full text-[10px] text-slate-500 sm:basis-auto">exp {expiresAt}</span>}
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

  // Reset when opening with a different record
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
        <button className="btn-primary" onClick={submit} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
      </div>
    </Modal>
  );
}
