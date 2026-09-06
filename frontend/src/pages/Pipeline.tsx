import { useEffect, useState, type ReactNode } from "react";
import { api, type ApiPayload, type CardReference, type CatalogEntry, type HeldAction, type LadderAlternative, type PipelineCard, type PipelineResponse } from "../lib/api";
import type { Flash } from "../App";
import { Banner, EmptyState, RareBadge, SectionTitle, Spinner, StatusBadge, cardName, decisionCondition, decisionDisplayStatus, fmtMoney } from "../components/ui";
import { NextMove } from "../components/NextMove";
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
  const [data, setData] = useState<PipelineResponse | null>(null);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [references, setReferences] = useState<CardReference[]>([]);
  const [loading, setLoading] = useState(true);
  const [formOpen, setFormOpen] = useState(false);
  const [prefill, setPrefill] = useState<ApiPayload | null>(null);
  const [mobileTab, setMobileTab] = useState<"next" | "keep">("next");

  const load = () => {
    setLoading(true);
    Promise.all([api.pipeline(user), api.catalog(user), api.cardReferences()])
      .then(([p, c, refs]) => {
        setData(p);
        setCatalog(c);
        setReferences(refs);
      })
      .catch((e) => flash("error", e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, [user, bump]);

  const recordApplication = (c: PipelineCard) => {
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

  const onSubmit = async (payload: ApiPayload) => {
    await api.createCard(payload);
    flash("info", `${cardName(payload)} recorded for ${user} - eligibility & pipeline recomputed.`);
    setFormOpen(false);
    load();
  };

  if (loading && !data) return <Spinner />;
  const f24 = data?.five_24;
  const next = data?.next_cards ?? [];
  const needsData = data?.needs_data ?? [];
  const alternates = data?.alternate_strategies ?? [];
  const actions = data?.held_actions ?? [];
  const canonical = data?.household_next_move;
  const referenceByKey = new Map(references.map((row) => [row.canonical_key, row]));
  const applyUrlFor = (id: number) => {
    const catalogRow = catalog.find((row) => row.id === id);
    const reference = catalogRow?.canonical_key ? referenceByKey.get(catalogRow.canonical_key) : null;
    return reference?.issuer_url || reference?.offer_url || catalogRow?.source_url || null;
  };

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

      <NextMove projection={canonical} />

      <div className="grid grid-cols-2 gap-1 rounded-lg border border-ink-400/60 bg-ink-800 p-1 lg:hidden">
        <button
          className={`rounded-md px-3 py-1.5 text-sm font-medium ${mobileTab === "next" ? "bg-ink-600 text-cyan-accent" : "text-slate-400"}`}
          onClick={() => setMobileTab("next")}
        >
          Open <span className="text-xs font-semibold opacity-70">{next.length}</span>
        </button>
        <button
          className={`rounded-md px-3 py-1.5 text-sm font-medium ${mobileTab === "keep" ? "bg-ink-600 text-cyan-accent" : "text-slate-400"}`}
          onClick={() => setMobileTab("keep")}
        >
          Manage <span className="text-xs font-semibold opacity-70">{actions.length}</span>
        </button>
      </div>

      <div className="lg:hidden">
        {mobileTab === "next" ? (
          <MobileSection title="Open" count={next.length}>
            {next.length === 0 ? (
              <EmptyState title="No eligible cards" hint="Run refresh or wait for eligibility blocks to clear." />
            ) : (
              <div className="space-y-2">
                {next.map((c) => (
                  <NextRow
                    key={c.id}
                    card={c}
                    applyUrl={applyUrlFor(c.id)}
                    onRecord={() => recordApplication(c)}
                  />
                ))}
              </div>
            )}
          </MobileSection>
        ) : (
          <div className="space-y-4">
            <MobileSection title="Manage" count={actions.length}>
              {actions.length === 0 ? (
                <EmptyState title="No held-card actions" hint="Add held cards to get renewal, downgrade, and cancel guidance." />
              ) : (
                <div className="space-y-2">
                  {actions.map((a) => (
                    <ActionRow key={a.id} action={a} />
                  ))}
                </div>
              )}
            </MobileSection>
            {alternates.length > 0 && (
              <MobileSection title="Ladder" count={alternates.length}>
                <div className="space-y-2">
                  {alternates.map((c) => (
                    <NeedsDataRow key={`alt-${c.id}`} card={c} variant="ladder" />
                  ))}
                </div>
              </MobileSection>
            )}
          </div>
        )}
      </div>

      <div className="hidden gap-6 lg:grid lg:grid-cols-2">
        <div>
          <div className="mb-3">
            <h3 className="text-sm font-semibold text-slate-100">Open Next</h3>
            <p className="mt-0.5 text-xs text-slate-500">Ordered apply queue. Record the application when you apply.</p>
          </div>
          {next.length === 0 ? (
            <EmptyState title="No eligible cards" hint="Either the catalog is empty or nothing is currently eligible. Run discovery/refresh, or wait for blocks to clear." />
          ) : (
            <div className="soft-scroll max-h-[66vh] space-y-2 pr-1">
              {next.map((c) => (
                <NextRow
                  key={c.id}
                  card={c}
                  applyUrl={applyUrlFor(c.id)}
                  onRecord={() => recordApplication(c)}
                />
              ))}
            </div>
          )}
          {(needsData.length > 0 || alternates.length > 0) && (
            <div className="mt-6 grid gap-4 xl:grid-cols-2">
              {needsData.length > 0 && (
                <div>
                  <SectionTitle title="Needs Data" subtitle="Excluded from apply queue until verified." />
                  <div className="soft-scroll max-h-[32vh] space-y-2 pr-1">
                    {needsData.map((c) => (
                      <NeedsDataRow key={c.id} card={c} />
                    ))}
                  </div>
                </div>
              )}
              {alternates.length > 0 && (
                <div>
                  <SectionTitle title="Ladder Alternatives" subtitle="Same-family review before opening." />
                  <div className="soft-scroll max-h-[32vh] space-y-2 pr-1">
                    {alternates.map((c) => (
                      <NeedsDataRow key={`alt-${c.id}`} card={c} variant="ladder" />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <div>
          <div className="mb-3">
            <h3 className="text-sm font-semibold text-slate-100">Manage Existing</h3>
            <p className="mt-0.5 text-xs text-slate-500">Renew, downgrade, cancel, or requeue guidance.</p>
          </div>
          {actions.length === 0 ? (
            <EmptyState title="No held cards" hint="Add cards on the Dashboard to get retention/downgrade guidance." />
          ) : (
            <div className="soft-scroll max-h-[66vh] space-y-2 pr-1">
              {actions.map((a) => (
                <ActionRow key={a.id} action={a} />
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
        references={references}
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
        <span className="text-xs font-semibold text-slate-500">{count}</span>
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

function NextRow({ card, applyUrl, onRecord }: { card: PipelineCard; applyUrl?: string | null; onRecord: () => void }) {
  const [open, setOpen] = useState(false);
  const displayStatus = decisionDisplayStatus(card);
  const actionableUrl = displayStatus === "APPLY NOW" ? applyUrl : null;
  return (
    <div className="rounded-lg border border-ink-400/60 bg-ink-700/60 px-3 py-2">
      <button className="flex w-full items-start gap-2 text-left" onClick={() => setOpen((value) => !value)}>
        <div className="mt-0.5 w-6 shrink-0 text-center text-xs font-semibold text-cyan-accent">{card.rank}</div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-slate-100">{cardName(card)}</div>
              <div className="truncate text-[11px] text-slate-500">
                {card.issuer} - {card.ownership}{card.currency ? ` - ${card.currency}` : ""}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {actionableUrl ? (
                <a href={actionableUrl} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>
                  <StatusBadge status={displayStatus} />
                </a>
              ) : (
                <StatusBadge status={displayStatus} />
              )}
              {card.is_exceptional && <RareBadge />}
              <span className={`text-lg text-slate-500 transition-transform ${open ? "rotate-90" : ""}`}>&rsaquo;</span>
            </div>
          </div>
          {card.reason && <div className="line-clamp-1 mt-1 text-[11px] leading-tight text-slate-500">{shortText(card.reason, 72)}</div>}
          {decisionCondition(card) && <div className="line-clamp-2 mt-1 text-[11px] leading-tight text-amber-200">{shortText(decisionCondition(card), 120)}</div>}
          <div className="mt-1.5 flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-baseline gap-2">
              <div className="text-sm font-semibold text-slate-200">{fmtMoney(card.offer_value)}</div>
              <div className="text-[10px] text-slate-500">peak {card.peak_score}</div>
            </div>
          </div>
        </div>
      </button>
      {open && (
        <div className="mt-2 space-y-2 border-t border-ink-400/50 pt-2 text-xs text-slate-400">
          <div>{card.reason}</div>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            <span>Offer value {fmtMoney(card.offer_value)}</span>
            <span>Peak score {card.peak_score}</span>
            {card.current_offer_min_spend ? <span>Spend {fmtMoney(card.current_offer_min_spend)} / {card.current_offer_window_months ?? "?"} mo</span> : null}
          </div>
          {displayStatus === "APPLY NOW" && <button className="btn-success h-8 px-3 text-xs" onClick={onRecord}>Record application</button>}
        </div>
      )}
    </div>
  );
}

function ActionRow({ action }: { action: HeldAction }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-ink-400/60 bg-ink-700/60 px-3 py-2">
      <button className="flex w-full items-start justify-between gap-3 text-left" onClick={() => setOpen((value) => !value)}>
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-slate-100">{cardName(action)}</div>
          <div className="truncate text-[11px] text-slate-500">{action.issuer}</div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className={`text-xs font-semibold uppercase tracking-wide ${ACTION_STYLE[action.action] ?? "text-slate-300"}`}>
            {actionLabel(action.action)}
          </div>
          <span className={`text-lg text-slate-500 transition-transform ${open ? "rotate-90" : ""}`}>&rsaquo;</span>
        </div>
      </button>
      {action.reason && <div className="line-clamp-2 mt-1 text-[11px] leading-tight text-slate-400">{shortText(action.reason)}</div>}
      {open && (
        <div className="mt-2 space-y-2 border-t border-ink-400/50 pt-2 text-xs text-slate-400">
          <div>{action.reason}</div>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            <span>Annual fee {action.annual_fee ? fmtMoney(action.annual_fee) : "none"}</span>
            <span>Renewal {action.renewal_date ?? "none"}</span>
            {action.eligible_again_date ? <span>Eligible again {action.eligible_again_date}</span> : null}
          </div>
        </div>
      )}
      {open && action.ladder_alternatives?.length ? (
        <div className="mt-2 space-y-1">
          {action.ladder_alternatives.slice(0, 2).map((alt: LadderAlternative) => (
            <div key={alt.id} className="rounded-md border border-cyan-accent/20 bg-cyan-accent/5 px-2 py-1 text-[11px] text-cyan-100">
              Same-family review: {cardName(alt)} - {fmtMoney(alt.offer_value)}, peak {alt.peak_score}, {alt.status}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function NeedsDataRow({ card, variant = "needs_data" }: { card: PipelineCard; variant?: "needs_data" | "ladder" }) {
  const isLadder = variant === "ladder";
  return (
    <div
      className={`rounded-lg border px-3 py-2 ${
        isLadder
          ? "pipeline-ladder-row border-violet-400/25 bg-violet-400/5"
          : "pipeline-needs-data-row border-ink-400/60 bg-ink-700/60"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-slate-100">{cardName(card)}</div>
          <div className="truncate text-[11px] text-slate-500">
            {card.issuer} - {card.ownership}{card.currency ? ` - ${card.currency}` : ""}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <StatusBadge status={decisionDisplayStatus(card)} />
          {card.is_exceptional && <RareBadge />}
        </div>
      </div>
      <div className={`line-clamp-2 mt-1 text-[11px] leading-tight ${isLadder ? "pipeline-ladder-reason text-slate-400" : "text-slate-400"}`}>
        {shortText(card.reason)}
      </div>
      {decisionCondition(card) && <div className="line-clamp-2 mt-1 text-[11px] leading-tight text-amber-200">{shortText(decisionCondition(card), 120)}</div>}
    </div>
  );
}
