import { FormEvent, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Flash } from "../App";
import { Banner, Card, EmptyState, Field, SectionTitle, Spinner, fmtMoney, fmtNum } from "../components/ui";

type TargetForm = {
  user: string;
  name: string;
  origin: string;
  destination: string;
  region: string;
  cabin_or_tier: string;
  preferred_programs: string;
  est_cost_points: string;
  target_value_cash: string;
  buy_points_cpp: string;
  travel_start_date: string;
  travel_end_date: string;
  passenger_count: string;
  frequency: string;
  priority: string;
  notes: string;
};

type PartnerForm = {
  from_currency: string;
  to_program: string;
  ratio: string;
  source_url: string;
};

const EMPTY_TARGET: TargetForm = {
  user: "Household",
  name: "",
  origin: "",
  destination: "",
  region: "",
  cabin_or_tier: "",
  preferred_programs: "",
  est_cost_points: "",
  target_value_cash: "",
  buy_points_cpp: "",
  travel_start_date: "",
  travel_end_date: "",
  passenger_count: "1",
  frequency: "",
  priority: "3",
  notes: "",
};

const EMPTY_PARTNER: PartnerForm = {
  from_currency: "",
  to_program: "",
  ratio: "1:1",
  source_url: "",
};

const DEFAULT_USERS = ["User A", "User B"];
const CABINS = ["Any", "Economy", "Premium Economy", "Business", "First", "Hotel"];
const REGIONS = [
  "Flexible",
  "North America",
  "Hawaii",
  "Caribbean",
  "Mexico",
  "Europe",
  "Japan",
  "Asia",
  "Middle East",
  "Africa",
  "Oceania",
  "South America",
];
const FREQUENCIES = ["One time", "Annual", "Flexible"];
const COMMON_PROGRAMS = [
  "United MileagePlus",
  "Air Canada Aeroplan",
  "Air France-KLM Flying Blue",
  "Virgin Atlantic Flying Club",
  "British Airways Avios",
  "American AAdvantage",
  "Alaska Mileage Plan",
  "Delta SkyMiles",
  "World of Hyatt",
  "Marriott Bonvoy",
  "Hilton Honors",
  "Chase Ultimate Rewards",
  "Amex Membership Rewards",
  "Capital One Miles",
];
const COMMON_CURRENCIES = [
  "Chase Ultimate Rewards",
  "Amex Membership Rewards",
  "Capital One Miles",
  "United MileagePlus",
  "World of Hyatt",
  "Marriott Bonvoy",
  "Delta SkyMiles",
];
const RATIOS = ["1:1", "2:1", "3:1", "1:2"];

function intOrNull(value: string) {
  const n = Number(value);
  return Number.isFinite(n) && value.trim() !== "" ? Math.round(n) : null;
}

function floatOrNull(value: string) {
  const n = Number(value);
  return Number.isFinite(n) && value.trim() !== "" ? n : null;
}

function targetPayload(form: TargetForm) {
  const startDate = dateOnly(form.travel_start_date);
  const endDate = dateOnly(form.travel_end_date);
  return {
    user: form.user.trim() || "Household",
    name: form.name.trim(),
    origin: form.origin.trim() || null,
    destination: form.destination.trim() || null,
    region: form.region.trim() || null,
    cabin_or_tier: form.cabin_or_tier.trim() || null,
    preferred_programs: form.preferred_programs.trim() || null,
    est_cost_points: intOrNull(form.est_cost_points),
    target_value_cash: floatOrNull(form.target_value_cash),
    buy_points_cpp: floatOrNull(form.buy_points_cpp),
    travel_start_date: startDate || null,
    travel_end_date: endDate || null,
    passenger_count: intOrNull(form.passenger_count),
    frequency: form.frequency.trim() || null,
    priority: intOrNull(form.priority),
    notes: form.notes.trim() || null,
  };
}

function partnerPayload(form: PartnerForm) {
  return {
    from_currency: form.from_currency.trim(),
    to_program: form.to_program.trim(),
    ratio: form.ratio.trim() || "1:1",
    source_url: form.source_url.trim() || null,
  };
}

function dateOnly(value: any): string {
  if (!value) return "";
  const text = String(value);
  const match = text.match(/^\d{4}-\d{2}-\d{2}/);
  return match ? match[0] : "";
}

export function Redemption({ bump, flash }: { user: string; bump: number; flash: Flash }) {
  const [data, setData] = useState<any>(null);
  const [users, setUsers] = useState<string[]>(DEFAULT_USERS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [targetForm, setTargetForm] = useState<TargetForm>(EMPTY_TARGET);
  const [partnerForm, setPartnerForm] = useState<PartnerForm>(EMPTY_PARTNER);
  const [editingTargetId, setEditingTargetId] = useState<number | null>(null);
  const [editingPartnerId, setEditingPartnerId] = useState<number | null>(null);
  const [targetFormOpen, setTargetFormOpen] = useState(false);
  const [partnerFormOpen, setPartnerFormOpen] = useState(false);

  const load = () => {
    setLoading(true);
    Promise.all([api.redemption(), api.users()])
      .then(([redemption, loadedUsers]) => {
        setData(redemption);
        setUsers(loadedUsers.length ? loadedUsers : DEFAULT_USERS);
      })
      .catch((e) => flash("error", e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, [bump]);

  const saveTarget = async (e: FormEvent) => {
    e.preventDefault();
    const payload = targetPayload(targetForm);
    if (!payload.name) {
      flash("warn", "Target name is required.");
      return;
    }
    setSaving(true);
    try {
      const updated = editingTargetId
        ? await api.updateRedemptionTarget(editingTargetId, payload)
        : await api.createRedemptionTarget(payload);
      setData(updated);
      setTargetForm(EMPTY_TARGET);
      setEditingTargetId(null);
      setTargetFormOpen(false);
      flash("info", editingTargetId ? "Redemption target updated." : "Redemption target added.");
    } catch (err: any) {
      flash("error", err.message);
    } finally {
      setSaving(false);
    }
  };

  const savePartner = async (e: FormEvent) => {
    e.preventDefault();
    const payload = partnerPayload(partnerForm);
    if (!payload.from_currency || !payload.to_program) {
      flash("warn", "Transfer partner needs a source currency and destination program.");
      return;
    }
    setSaving(true);
    try {
      const updated = editingPartnerId
        ? await api.updateTransferPartner(editingPartnerId, payload)
        : await api.createTransferPartner(payload);
      setData(updated);
      setPartnerForm(EMPTY_PARTNER);
      setEditingPartnerId(null);
      setPartnerFormOpen(false);
      flash("info", editingPartnerId ? "Transfer partner updated." : "Transfer partner added.");
    } catch (err: any) {
      flash("error", err.message);
    } finally {
      setSaving(false);
    }
  };

  const editTarget = (target: any) => {
    setEditingTargetId(target.id);
    setTargetFormOpen(true);
    setTargetForm({
      user: target.user ?? "Household",
      name: target.name ?? "",
      origin: target.origin ?? "",
      destination: target.destination ?? "",
      region: target.region ?? "",
      cabin_or_tier: target.cabin_or_tier ?? "",
      preferred_programs: target.preferred_programs ?? "",
      est_cost_points: target.est_cost_points == null ? "" : String(target.est_cost_points),
      target_value_cash: target.target_value_cash == null ? "" : String(target.target_value_cash),
      buy_points_cpp: target.buy_points_cpp == null ? "" : String(target.buy_points_cpp),
      travel_start_date: dateOnly(target.travel_start_date),
      travel_end_date: dateOnly(target.travel_end_date),
      passenger_count: target.passenger_count == null ? "1" : String(target.passenger_count),
      frequency: target.frequency ?? "",
      priority: target.priority == null ? "" : String(target.priority),
      notes: target.notes ?? "",
    });
  };

  const editPartner = (partner: any) => {
    setEditingPartnerId(partner.id);
    setPartnerFormOpen(true);
    setPartnerForm({
      from_currency: partner.from_currency ?? "",
      to_program: partner.to_program ?? "",
      ratio: partner.ratio ?? "1:1",
      source_url: partner.source_url ?? "",
    });
  };

  const deleteTarget = async (target: any) => {
    if (!confirm(`Delete target ${target.name}?`)) return;
    const updated = await api.deleteRedemptionTarget(target.id);
    setData(updated);
    if (editingTargetId === target.id) {
      setEditingTargetId(null);
      setTargetForm(EMPTY_TARGET);
      setTargetFormOpen(false);
    }
    flash("info", "Redemption target deleted.");
  };

  const deletePartner = async (partner: any) => {
    if (!confirm(`Delete ${partner.from_currency} to ${partner.to_program}?`)) return;
    const updated = await api.deleteTransferPartner(partner.id);
    setData(updated);
    if (editingPartnerId === partner.id) {
      setEditingPartnerId(null);
      setPartnerForm(EMPTY_PARTNER);
      setPartnerFormOpen(false);
    }
    flash("info", "Transfer partner deleted.");
  };

  const toggleWithoutScrollJump = (setter: (updater: (open: boolean) => boolean) => void) => {
    const y = window.scrollY;
    setter((open) => !open);
    requestAnimationFrame(() => {
      window.scrollTo({ top: y, left: 0, behavior: "auto" });
      requestAnimationFrame(() => window.scrollTo({ top: y, left: 0, behavior: "auto" }));
    });
  };

  if (loading && !data) return <Spinner />;

  const progressRows: any[] = data?.progress ?? [];
  const balances: any[] = data?.balances ?? [];
  const partners: any[] = data?.transfer_partners ?? [];
  const currencyOptions = Array.from(
    new Set([...balances.map((row) => String(row.currency || "")).filter(Boolean), ...COMMON_CURRENCIES]),
  ).sort();
  const programOptions = Array.from(
    new Set([
      ...COMMON_PROGRAMS,
      ...partners.map((partner) => String(partner.to_program || "")).filter(Boolean),
      ...progressRows.flatMap((row) => row.programs ?? []),
    ]),
  ).sort();

  return (
    <div className="space-y-6">
      <SectionTitle
        title="Redemption"
        subtitle="Household progress toward trip goals. seats.aero is award availability/search only; booking remains manual."
      />

      <Banner kind={data?.live_enabled ? "info" : "warn"}>{data?.note}</Banner>
      <datalist id="redemption-programs">
        {programOptions.map((program) => (
          <option key={program} value={program} />
        ))}
      </datalist>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
        <div className="space-y-4">
          <Card>
            <div className="mb-3 flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
              <div className="font-semibold text-slate-100">Goal Progress</div>
              <div className="text-xs text-slate-500">{data?.mode === "live" ? "Live availability" : "Benchmark estimates"}</div>
            </div>
            {progressRows.length === 0 ? (
              <EmptyState title="No redemption targets" hint="Add a trip goal to measure household progress and transfer options." />
            ) : (
              <div className="space-y-2">
                {progressRows.map((row) => (
                  <div key={row.target.id} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-3 py-2">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <div className="truncate font-medium text-slate-100">{row.target.name}</div>
                        <div className="mt-0.5 text-xs text-slate-500">
                          {row.programs?.join(", ") || "Program needed"} | {row.target.cabin_or_tier || "Any cabin"}
                        </div>
                        <div className="mt-0.5 text-[11px] text-slate-500">
                          {[row.target.origin, row.target.destination || row.target.region].filter(Boolean).join(" to ") || "Route flexible"}
                          {row.target.travel_start_date ? ` | ${row.target.travel_start_date}${row.target.travel_end_date ? ` to ${row.target.travel_end_date}` : ""}` : ""}
                          {row.target.passenger_count ? ` | ${row.target.passenger_count} traveler${row.target.passenger_count === 1 ? "" : "s"}` : ""}
                        </div>
                      </div>
                      <div className="text-left sm:text-right">
                        <div className="text-sm font-semibold tabular-nums text-cyan-accent">{row.progress_pct}%</div>
                        <div className="text-[11px] text-slate-500">{fmtNum(row.available_points)} / {fmtNum(row.needed_points)} pts</div>
                      </div>
                    </div>
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-900">
                      <div className="h-full rounded-full bg-cyan-accent" style={{ width: `${Math.min(100, row.progress_pct || 0)}%` }} />
                    </div>
                    <div className="mt-2 grid gap-2 text-xs sm:grid-cols-3">
                      <div>
                        <div className="text-slate-500">More needed</div>
                        <div className="font-semibold tabular-nums text-slate-200">{fmtNum(row.more_points_needed)}</div>
                      </div>
                      <div>
                        <div className="text-slate-500">Trip value</div>
                        <div className="font-semibold tabular-nums text-slate-200">
                          {row.realized_cpp == null ? "Needs value" : `${row.realized_cpp.toFixed(2)} cpp`}
                        </div>
                      </div>
                      <div>
                        <div className="text-slate-500">Buy points</div>
                        <div className={row.buy_points_worth_it ? "text-emerald-300" : "text-slate-400"}>
                          {row.buy_points_cpp == null ? "Needs price" : row.buy_points_worth_it ? "Worth checking" : "Not favored"}
                        </div>
                      </div>
                    </div>
                    {row.best_transfer && (
                      <div className="mt-2 rounded-md border border-cyan-accent/20 bg-cyan-accent/5 px-2 py-1 text-xs text-cyan-100">
                        Transfer {fmtNum(row.best_transfer.source_points)} {row.best_transfer.from_currency} {row.best_transfer.ratio} to {row.best_transfer.to_program}.
                      </div>
                    )}
                    <div className="mt-2 text-xs leading-relaxed text-slate-400">{row.guidance}</div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <button className="btn-ghost py-1 text-xs" onClick={() => editTarget(row.target)}>Edit</button>
                      <button className="btn-danger py-1 text-xs" onClick={() => deleteTarget(row.target)}>Delete</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card>
            <div className="mb-3 font-semibold text-slate-100">Household Balances</div>
            {balances.length === 0 ? (
              <div className="text-sm text-slate-500">Add balances in Profiles to power redemption progress.</div>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {balances.map((row) => (
                  <div key={row.currency} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-3 py-2">
                    <div className="truncate text-sm font-medium text-slate-100">{row.currency}</div>
                    <div className="mt-1 flex items-baseline justify-between gap-3">
                      <span className="text-sm font-semibold text-cyan-accent tabular-nums">{fmtNum(row.balance)}</span>
                      <span className="text-xs text-slate-500">{fmtMoney(row.value)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        <div className="space-y-4">
          <Card className="p-0">
            <button
              type="button"
              className="flex w-full items-center justify-between gap-3 px-3 py-3 text-left sm:px-4"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => toggleWithoutScrollJump(setTargetFormOpen)}
            >
              <div>
                <div className="font-semibold text-slate-100">{editingTargetId ? "Edit Target" : "Add Target"}</div>
                <div className="mt-0.5 text-xs text-slate-500">Trip goal, estimated points, dates, and cash value.</div>
              </div>
              <span className={`text-lg text-slate-500 transition-transform ${targetFormOpen ? "rotate-90" : ""}`}>&rsaquo;</span>
            </button>
            {targetFormOpen && (
            <form onSubmit={saveTarget} className="space-y-3 border-t border-ink-400/60 p-3 sm:p-4">
              <Field label="Owner">
                <select className="input" value={targetForm.user} onChange={(e) => setTargetForm({ ...targetForm, user: e.target.value })}>
                  {["Household", ...users].map((value) => (
                    <option key={value} value={value}>{value}</option>
                  ))}
                </select>
              </Field>
              <Field label="Name">
                <input className="input" value={targetForm.name} onChange={(e) => setTargetForm({ ...targetForm, name: e.target.value })} placeholder="Japan business class" />
              </Field>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Origin">
                  <input className="input" value={targetForm.origin} onChange={(e) => setTargetForm({ ...targetForm, origin: e.target.value.toUpperCase() })} placeholder="LAX" />
                </Field>
                <Field label="Destination">
                  <input className="input" value={targetForm.destination} onChange={(e) => setTargetForm({ ...targetForm, destination: e.target.value })} placeholder="Tokyo" />
                </Field>
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Program(s)">
                  <input className="input" list="redemption-programs" value={targetForm.preferred_programs} onChange={(e) => setTargetForm({ ...targetForm, preferred_programs: e.target.value })} placeholder="United MileagePlus" />
                </Field>
                <Field label="Region">
                  <select className="input" value={targetForm.region} onChange={(e) => setTargetForm({ ...targetForm, region: e.target.value })}>
                    <option value="">Select region</option>
                    {REGIONS.map((value) => (
                      <option key={value} value={value === "Flexible" ? "" : value}>{value}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Cabin">
                  <select className="input" value={targetForm.cabin_or_tier} onChange={(e) => setTargetForm({ ...targetForm, cabin_or_tier: e.target.value })}>
                    <option value="">Any cabin</option>
                    {CABINS.filter((value) => value !== "Any").map((value) => (
                      <option key={value} value={value}>{value}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Start date">
                  <input className="input" type="date" value={targetForm.travel_start_date} onChange={(e) => setTargetForm({ ...targetForm, travel_start_date: e.target.value })} />
                </Field>
                <Field label="End date">
                  <input className="input" type="date" value={targetForm.travel_end_date} onChange={(e) => setTargetForm({ ...targetForm, travel_end_date: e.target.value })} />
                </Field>
                <Field label="Travelers">
                  <select className="input" value={targetForm.passenger_count} onChange={(e) => setTargetForm({ ...targetForm, passenger_count: e.target.value })}>
                    {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((value) => (
                      <option key={value} value={String(value)}>{value}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Needed points">
                  <input className="input" inputMode="numeric" value={targetForm.est_cost_points} onChange={(e) => setTargetForm({ ...targetForm, est_cost_points: e.target.value })} placeholder="100000" />
                </Field>
                <Field label="Frequency">
                  <select className="input" value={targetForm.frequency} onChange={(e) => setTargetForm({ ...targetForm, frequency: e.target.value })}>
                    <option value="">Select frequency</option>
                    {FREQUENCIES.map((value) => (
                      <option key={value} value={value}>{value}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Cash value">
                  <input className="input" inputMode="decimal" value={targetForm.target_value_cash} onChange={(e) => setTargetForm({ ...targetForm, target_value_cash: e.target.value })} placeholder="3000" />
                </Field>
                <Field label="Buy cpp">
                  <input className="input" inputMode="decimal" value={targetForm.buy_points_cpp} onChange={(e) => setTargetForm({ ...targetForm, buy_points_cpp: e.target.value })} placeholder="2.0" />
                </Field>
                <Field label="Priority">
                  <select className="input" value={targetForm.priority} onChange={(e) => setTargetForm({ ...targetForm, priority: e.target.value })}>
                    {[1, 2, 3, 4, 5].map((value) => (
                      <option key={value} value={String(value)}>{value}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <Field label="Notes">
                <textarea className="input min-h-[70px]" value={targetForm.notes} onChange={(e) => setTargetForm({ ...targetForm, notes: e.target.value })} />
              </Field>
              <div className="flex flex-wrap gap-2">
                <button className="btn-primary" disabled={saving}>{editingTargetId ? "Save Target" : "Add Target"}</button>
                {editingTargetId && (
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => {
                      setEditingTargetId(null);
                      setTargetForm(EMPTY_TARGET);
                      setTargetFormOpen(false);
                    }}
                  >
                    Cancel
                  </button>
                )}
              </div>
            </form>
            )}
          </Card>

          <Card className="p-0">
            <button
              type="button"
              className="flex w-full items-center justify-between gap-3 px-3 py-3 text-left sm:px-4"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => toggleWithoutScrollJump(setPartnerFormOpen)}
            >
              <div>
                <div className="font-semibold text-slate-100">{editingPartnerId ? "Edit Transfer Partner" : "Add Transfer Partner"}</div>
                <div className="mt-0.5 text-xs text-slate-500">Map transferable currencies to travel programs.</div>
              </div>
              <span className={`text-lg text-slate-500 transition-transform ${partnerFormOpen ? "rotate-90" : ""}`}>&rsaquo;</span>
            </button>
            {partnerFormOpen && (
            <form onSubmit={savePartner} className="space-y-3 border-t border-ink-400/60 p-3 sm:p-4">
              <Field label="From currency">
                <select className="input" value={partnerForm.from_currency} onChange={(e) => setPartnerForm({ ...partnerForm, from_currency: e.target.value })}>
                  <option value="">Select currency</option>
                  {currencyOptions.map((value) => (
                    <option key={value} value={value}>{value}</option>
                  ))}
                </select>
              </Field>
              <Field label="To program">
                <input className="input" list="redemption-programs" value={partnerForm.to_program} onChange={(e) => setPartnerForm({ ...partnerForm, to_program: e.target.value })} placeholder="United MileagePlus" />
              </Field>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Ratio">
                  <select className="input" value={partnerForm.ratio} onChange={(e) => setPartnerForm({ ...partnerForm, ratio: e.target.value })}>
                    {RATIOS.map((value) => (
                      <option key={value} value={value}>{value}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Source URL">
                  <input className="input" value={partnerForm.source_url} onChange={(e) => setPartnerForm({ ...partnerForm, source_url: e.target.value })} />
                </Field>
              </div>
              <div className="flex flex-wrap gap-2">
                <button className="btn-primary" disabled={saving}>{editingPartnerId ? "Save Partner" : "Add Partner"}</button>
                {editingPartnerId && (
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => {
                      setEditingPartnerId(null);
                      setPartnerForm(EMPTY_PARTNER);
                      setPartnerFormOpen(false);
                    }}
                  >
                    Cancel
                  </button>
                )}
              </div>
            </form>
            )}

          </Card>

          {partners.length > 0 && (
            <Card className="p-0">
              <div className="border-b border-ink-400/60 px-3 py-2 text-sm font-semibold text-slate-100 sm:px-4">
                Transfer Partners
              </div>
              <div className="space-y-2 p-3 sm:p-4">
                {partners.map((partner) => (
                  <div key={partner.id} className="rounded-lg border border-ink-400/50 bg-ink-800/40 px-3 py-2">
                    <div className="text-sm text-slate-100">{partner.from_currency} to {partner.to_program}</div>
                    <div className="text-xs text-slate-500">{partner.ratio || "1:1"}</div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <button className="btn-ghost py-1 text-xs" onClick={() => editPartner(partner)}>Edit</button>
                      <button className="btn-danger py-1 text-xs" onClick={() => deletePartner(partner)}>Delete</button>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
