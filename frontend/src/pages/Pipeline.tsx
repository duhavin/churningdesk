import { useEffect, useState, type ReactNode } from "react";
import { api, type CatalogEntry } from "../lib/api";
import type { Flash } from "../App";
import { Banner, Card, EmptyState, SectionTitle, Spinner, StatusBadge, fmtMoney } from "../components/ui";
import { CardForm } from "../components/CardForm";

const ACTION_STYLE: Record<string, string> = {
  keep: "text-slate-300",
  downgrade: "text-amber-300",
  cancel: "text-rose-300",
  requeue: "text-cyan-accent",
  ladder_review: "text-violet-300",
};

const today = () => new Date().toISOString().slice(0, 10);
const addMonths = (date: string, months: number) => {
  const d = new Date(`${date}T00:00:00`);
  d.setMonth(d.getMonth() + months);
  return d.toISOString().slice(0, 10);
};

export function Pipeline({ user, bump, flash }: { user: string; bump: number; flash: Flash }) {
  const [data, setData] = useState<any>(null);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [formOpen, setFormOpen] = useState(false);
  const [prefill, setPrefill] = useState<Record<string, any> | null>(null);
  const [mobileTab, setMobileTab] = useState<"next" | "keep">("next");

  const load = () => {
    setLoading(true);
    Promise.all([api.pipeline(user), api.catalog(user)])
      .then(([p, c]) => {
        setData(p);
        setCatalog(c);
      })
      .catch((e) => flash("error", e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, [user, bump]);

  const recordApplication = (c: any) => {
    const cat = catalog.find((x) => x.id === c.id);
    const opened = today();
    const minSpend = c.current_offer_min_spend ?? cat?.current_offer_min_spend ?? "";
    const windowMonths = c.current_offer_window_months ?? cat?.current_offer_window_months ?? 3;
    setPrefill({
      issuer: c.issuer,
      product_name: c.product_name,
      product_id: c.id,
      ownership: c.ownership ?? cat?.ownership ?? "Personal",
      account_type: cat?.account_type ?? "Credit Card",
      currency: c.currency ?? cat?.currency ?? "",
      annual_fee: cat?.annual_fee ?? "",
      reports_to_personal_credit: cat?.reports_to_personal_credit ?? true,
      date_opened: opened,
      min_spend_requirement: minSpend,
      min_spend_progress: minSpend ? 0 : "",
      min_spend_deadline: minSpend ? addMonths(opened, windowMonths) : "",
    });
    setFormOpen(true);
  };

  const onSubmit = async (payload: any) => {
    await api.createCard(payload);
    flash("info", `${payload.product_name} recorded for ${user} — eligibility & pipeline recomputed.`);
    setFormOpen(false);
    load();
  };

  if (loading && !data) return <Spinner />;
  const f24 = data?.five_24;
  const next = data?.next_cards ?? [];
  const needsData = data?.needs_data ?? [];
  const actions = data?.held_actions ?? [];

  return (
    <div className="space-y-6">
      <SectionTitle title="Pipeline" subtitle="Next openings and keep/downgrade/cancel actions." />

      <Banner kind={f24?.under_524 ? "info" : "warn"}>
        {f24?.under_524 ? (
          <>
            <b>{user} is under 5/24 ({f24.count}/24).</b> Chase-first while you can — Chase approvals get harder at 5/24.
          </>
        ) : (
          <>
            <b>{user} is at/over 5/24 ({f24?.count}/24).</b> Hold on new Chase personal cards
            {f24?.earliest_drop_date ? ` until ${f24.earliest_drop_date}` : ""}; pursue non-Chase and Chase business (Ink).
          </>
        )}
      </Banner>

      <div className="grid grid-cols-2 gap-1 rounded-lg border border-ink-400/60 bg-ink-800 p-1 lg:hidden">
        <button
          className={`rounded-md px-3 py-1.5 text-sm font-medium ${mobileTab === "next" ? "bg-ink-600 text-cyan-accent" : "text-slate-400"}`}
          onClick={() => setMobileTab("next")}
        >
          Open <span className="font-mono text-xs opacity-70">{next.length}</span>
        </button>
        <button
          className={`rounded-md px-3 py-1.5 text-sm font-medium ${mobileTab === "keep" ? "bg-ink-600 text-cyan-accent" : "text-slate-400"}`}
          onClick={() => setMobileTab("keep")}
        >
          Manage <span className="font-mono text-xs opacity-70">{actions.length}</span>
        </button>
      </div>

      <div className="lg:hidden">
        {mobileTab === "next" ? (
          <MobileSection title="Open" count={next.length}>
            {next.length === 0 ? (
              <EmptyState title="No eligible cards" hint="Run refresh or wait for eligibility blocks to clear." />
            ) : (
              <div className="space-y-2">
                {next.map((c: any) => (
                  <NextRow key={c.id} card={c} onRecord={() => recordApplication(c)} />
                ))}
              </div>
            )}
            {needsData.length > 0 && (
              <div className="mt-4">
                <MobileSection title="Needs Data" count={needsData.length}>
                  <div className="space-y-2">
                    {needsData.map((c: any) => (
                      <NeedsDataRow key={c.id} card={c} />
                    ))}
                  </div>
                </MobileSection>
              </div>
            )}
          </MobileSection>
        ) : (
          <MobileSection title="Manage" count={actions.length}>
            {actions.length === 0 ? (
              <EmptyState title="No held-card actions" hint="Add held cards to get renewal, downgrade, and cancel guidance." />
            ) : (
              <div className="space-y-2">
                {actions.map((a: any) => (
                  <ActionRow key={a.id} action={a} />
                ))}
              </div>
            )}
          </MobileSection>
        )}
      </div>

      <div className="hidden gap-6 lg:grid lg:grid-cols-2">
        <div>
          <SectionTitle title="Open Next" subtitle="Ordered apply queue. Record the application when you apply." />
          {next.length === 0 ? (
            <EmptyState title="No eligible cards" hint="Either the catalog is empty or nothing is currently eligible. Run discovery/refresh, or wait for blocks to clear." />
          ) : (
            <div className="space-y-2">
              {next.map((c: any) => (
                <Card key={c.id} className="flex flex-col gap-3 sm:flex-row sm:items-start">
                  <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-ink-500 font-mono text-xs text-cyan-accent">
                    {c.rank}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-slate-100">{c.product_name}</span>
                      <StatusBadge status={c.status} />
                      {c.targeted_beats_public && (
                        <span className="chip bg-pink-accent/15 text-pink-accent">targeted ▲</span>
                      )}
                    </div>
                    <div className="text-[11px] text-slate-500">
                      {c.issuer} · {c.ownership}
                      {c.currency ? ` · ${c.currency}` : ""}
                    </div>
                    <div className="mt-1 text-sm text-slate-400">{c.reason}</div>
                    <button
                      className="btn-success mt-2 px-2 py-1 text-xs"
                      onClick={() => recordApplication(c)}
                    >
                      Record application →
                    </button>
                  </div>
                  <div className="shrink-0 text-left sm:text-right">
                    <div className="font-mono text-sm text-slate-200">{fmtMoney(c.offer_value)}</div>
                    <div className="text-[11px] text-slate-500">peak {c.peak_score}</div>
                  </div>
                </Card>
              ))}
            </div>
          )}
          {needsData.length > 0 && (
            <div className="mt-6">
              <SectionTitle title="Needs Data" subtitle="Cards excluded from the apply queue until offer/peak data is verified." />
              <div className="space-y-2">
                {needsData.map((c: any) => (
                  <NeedsDataRow key={c.id} card={c} />
                ))}
              </div>
            </div>
          )}
        </div>

        <div>
          <SectionTitle title="Manage Existing" subtitle="Renew, downgrade, cancel, or requeue guidance." />
          {actions.length === 0 ? (
            <EmptyState title="No held cards" hint="Add cards on the Dashboard to get retention/downgrade guidance." />
          ) : (
            <div className="space-y-2">
              {actions.map((a: any) => (
                <Card key={a.id} className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <div className="font-medium text-slate-100">{a.product_name}</div>
                    <div className="text-[11px] text-slate-500">{a.issuer}</div>
                    <div className="mt-1 text-sm text-slate-400">{a.reason}</div>
                  </div>
                  <div className={`shrink-0 text-sm font-semibold uppercase tracking-wide ${ACTION_STYLE[a.action] ?? "text-slate-300"}`}>
                    {actionLabel(a.action)}
                  </div>
                </Card>
              ))}
            </div>
          )}
        </div>
      </div>

      <CardForm
        open={formOpen}
        onClose={() => setFormOpen(false)}
        onSubmit={onSubmit}
        user={user}
        catalog={catalog}
        prefill={prefill}
      />
    </div>
  );
}

function MobileSection({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-300">{title}</h3>
        <span className="font-mono text-xs text-slate-500">{count}</span>
      </div>
      {children}
    </div>
  );
}

function shortText(value: string | null | undefined, max = 96) {
  const text = (value ?? "").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1).trim()}...`;
}

function actionLabel(action: string) {
  return (action ?? "").replace(/_/g, " ");
}

function NextRow({ card, onRecord }: { card: any; onRecord: () => void }) {
  return (
    <div className="rounded-lg border border-ink-400/60 bg-ink-700/60 px-3 py-2">
      <div className="flex items-start gap-2">
        <div className="mt-0.5 w-6 shrink-0 text-center font-mono text-xs text-cyan-accent">{card.rank}</div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-slate-100">{card.product_name}</div>
              <div className="truncate text-[11px] text-slate-500">
                {card.issuer} - {card.ownership}{card.currency ? ` - ${card.currency}` : ""}
              </div>
            </div>
            <StatusBadge status={card.status} />
          </div>
          {card.reason && <div className="line-clamp-1 mt-1 text-[11px] leading-tight text-slate-500">{shortText(card.reason, 72)}</div>}
          <div className="mt-1.5 flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-baseline gap-2">
              <div className="font-mono text-sm text-slate-200">{fmtMoney(card.offer_value)}</div>
              <div className="text-[10px] text-slate-500">peak {card.peak_score}</div>
            </div>
            <button className="btn-success px-2 py-0.5 text-xs" onClick={onRecord}>Record</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ActionRow({ action }: { action: any }) {
  return (
    <div className="rounded-lg border border-ink-400/60 bg-ink-700/60 px-3 py-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-slate-100">{action.product_name}</div>
          <div className="truncate text-[11px] text-slate-500">{action.issuer}</div>
        </div>
        <div className={`shrink-0 text-xs font-semibold uppercase tracking-wide ${ACTION_STYLE[action.action] ?? "text-slate-300"}`}>
          {actionLabel(action.action)}
        </div>
      </div>
      {action.reason && <div className="line-clamp-2 mt-1 text-[11px] leading-tight text-slate-400">{shortText(action.reason)}</div>}
    </div>
  );
}

function NeedsDataRow({ card }: { card: any }) {
  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-950/10 px-3 py-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-slate-100">{card.product_name}</div>
          <div className="truncate text-[11px] text-slate-500">
            {card.issuer} - {card.ownership}{card.currency ? ` - ${card.currency}` : ""}
          </div>
        </div>
        <StatusBadge status={card.status} />
      </div>
      <div className="line-clamp-2 mt-1 text-[11px] leading-tight text-amber-100/80">{shortText(card.reason)}</div>
    </div>
  );
}
