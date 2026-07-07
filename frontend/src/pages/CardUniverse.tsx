import { useEffect, useState } from "react";
import { api, type CatalogHealthResponse, type ProposedChange, type RunStatus } from "../lib/api";
import type { Flash } from "../App";
import { Banner, Card, SectionTitle, Spinner, cardName, issuerCardLabel } from "../components/ui";

function changeField(change: ProposedChange) {
  return change.field_label || String(change.field || "").replace(/_/g, " ");
}

function changeSource(change: ProposedChange) {
  return change.source_domain || change.source_url || "no source";
}

export function CardUniverse({ user, bump, flash }: { user: string; bump: number; flash: Flash }) {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [changes, setChanges] = useState<ProposedChange[]>([]);
  const [health, setHealth] = useState<CatalogHealthResponse | null>(null);
  const [discovered, setDiscovered] = useState<any[]>([]);
  const [watchlist, setWatchlist] = useState<any[]>([]);
  const [blacklist, setBlacklist] = useState<any[]>([]);
  const [valuations, setValuations] = useState<any[]>([]);
  const [sources, setSources] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const load = () => {
    setLoading(true);
    Promise.all([
      api.runStatus(),
      api.proposedChanges("pending"),
      api.discovered(),
      api.watchlist(),
      api.blacklist(),
      api.valuations(),
      api.sources(),
      api.catalogHealth(),
    ])
      .then(([st, ch, dc, wl, bl, vs, sr, healthResult]) => {
        setStatus(st);
        setChanges(ch);
        setDiscovered(dc);
        setWatchlist(wl);
        setBlacklist(bl);
        setValuations(vs);
        setSources(sr);
        setHealth(healthResult);
      })
      .catch((e) => flash("error", e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, [bump]);

  const run = async (fn: () => Promise<any>, label: string) => {
    setRunning(true);
    try {
      await fn();
      flash("info", `${label} complete.`);
      load();
    } catch (e: any) {
      flash("error", e.message);
    } finally {
      setRunning(false);
    }
  };

  const decide = async (id: number, decision: string) => {
    await api.decideChange(id, decision);
    flash("info", `Change ${decision}.`);
    load();
  };

  if (loading && !status) return <Spinner />;
  const llmOk = status?.llm_available;
  const healthRows = (health?.products ?? []).filter((row) => row.status !== "healthy").slice(0, 8);

  return (
    <div className="space-y-6">
      <SectionTitle title="Card Universe / Review" subtitle="Manage lists, catalog health, and approve LLM-extracted changes." />

      {!llmOk && (
        <Banner kind="warn">
          ANTHROPIC_API_KEY is not configured — discovery & refresh are disabled. The rest of the admin tools still work.
        </Banner>
      )}

      {/* Catalog health */}
      <Card>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="min-w-0">
            <div className="text-sm font-medium text-slate-100">Catalog health</div>
            <div className="text-[11px] text-slate-500">Held cards and high-impact catalog gaps first.</div>
          </div>
          {health && (
            <div className="ml-auto flex flex-wrap gap-1 text-[10px] text-slate-400">
              <span className="chip bg-ink-500">needs data {health.summary.needs_data}</span>
              <span className="chip bg-ink-500">review {health.summary.needs_review}</span>
              <span className="chip bg-ink-500">held gaps {health.summary.held_needs_data}</span>
            </div>
          )}
          <div className="flex w-full flex-wrap gap-2 sm:w-auto">
            <button
              className="btn-ghost flex-1 justify-center px-2 py-1 text-xs sm:flex-none"
              disabled={running}
              onClick={() => run(() => api.cleanupProposedChanges(), "Review cleanup")}
            >
              Clean review queue
            </button>
            <button
              className="btn-ghost flex-1 justify-center px-2 py-1 text-xs sm:flex-none"
              disabled={running}
              onClick={() => run(() => api.mergeCatalogDuplicates(), "Duplicate merge")}
            >
              Merge duplicates
            </button>
            <button
              className="btn-ghost flex-1 justify-center px-2 py-1 text-xs sm:flex-none"
              disabled={running}
              onClick={() => run(() => api.sanitizeCatalogText(), "Text cleanup")}
              title="Repair encoding damage and strip scrape junk from public card text"
            >
              Clean text
            </button>
          </div>
        </div>
        {healthRows.length === 0 ? (
          <div className="text-sm text-slate-500">No catalog health issues found.</div>
        ) : (
          <div className="soft-scroll max-h-[260px] space-y-2 pr-1">
            {healthRows.map((row) => (
              <div key={row.product_id} className="rounded-md border border-ink-400/60 bg-ink-900 px-3 py-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-slate-100">{row.display_name}</div>
                    <div className="mt-0.5 flex flex-wrap gap-1 text-[10px] text-slate-500">
                      <span>{row.status.replace(/_/g, " ")}</span>
                      <span>{row.health_score}/100</span>
                      {row.held_by.length > 0 && <span>held by {row.held_by.join(", ")}</span>}
                    </div>
                  </div>
                  <div className="shrink-0 text-right text-[11px] text-cyan-accent">{row.next_action}</div>
                </div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {row.issues.slice(0, 4).map((issue) => (
                    <span
                      key={issue.code}
                      className={`rounded-md border px-1.5 py-0.5 text-[10px] ${
                        issue.severity === "high"
                          ? "border-rose-300/30 bg-rose-300/10 text-rose-100"
                          : issue.severity === "medium"
                            ? "border-amber-300/30 bg-amber-300/10 text-amber-100"
                            : "border-ink-400 bg-ink-800 text-slate-400"
                      }`}
                    >
                      {issue.label}
                    </span>
                  ))}
                  {row.issues.length > 4 && <span className="text-[10px] text-slate-500">+{row.issues.length - 4} more</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Proposed changes queue */}
      <div>
        <SectionTitle title={`Proposed changes (${changes.length})`} subtitle="Delta-gated LLM extractions awaiting approval." />
        {changes.length === 0 ? (
          <Card className="text-sm text-slate-500">No pending changes.</Card>
        ) : (
          <>
          <div className="soft-scroll max-h-[58vh] space-y-2 pr-1 md:hidden">
            {changes.map((c) => (
              <Card key={c.id} className="space-y-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-medium text-slate-100">{c.product}</div>
                    <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{changeField(c)}</div>
                  </div>
                  <div className="shrink-0 text-xs text-slate-400">{c.confidence != null ? c.confidence.toFixed(2) : "-"}</div>
                </div>
                <div className="rounded-md border border-ink-400/50 bg-ink-900 p-2 text-xs">
                  <div className="text-[10px] uppercase tracking-wide text-slate-600">Current</div>
                  <div className="line-clamp-2 break-words text-slate-400">{c.old_preview ?? "-"}</div>
                  <div className="mt-2 text-[10px] uppercase tracking-wide text-slate-600">Proposed</div>
                  <div className="line-clamp-3 break-words font-medium text-cyan-accent">{c.new_preview ?? "-"}</div>
                </div>
                {c.review_note && (
                  <div className="rounded-md border border-amber-300/30 bg-amber-300/5 px-2 py-1 text-[11px] text-amber-100">
                    {c.review_note}
                  </div>
                )}
                <div className="truncate text-[11px] text-slate-500" title={c.source_url}>{changeSource(c)}</div>
                <div className="flex justify-end gap-3">
                  <button className="text-xs text-emerald-300 hover:underline" onClick={() => decide(c.id, "approved")}>
                    approve
                  </button>
                  <button className="text-xs text-rose-300 hover:underline" onClick={() => decide(c.id, "rejected")}>
                    reject
                  </button>
                </div>
              </Card>
            ))}
          </div>
          <Card className="soft-scroll hidden max-h-[60vh] overflow-x-auto p-0 md:block">
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Product</th>
                  <th className="th">Field</th>
                  <th className="th">Current / Proposed</th>
                  <th className="th">Conf.</th>
                  <th className="th">Source</th>
                  <th className="th"></th>
                </tr>
              </thead>
              <tbody>
                {changes.map((c) => (
                  <tr key={c.id}>
                    <td className="td text-slate-200">{c.product}</td>
                    <td className="td text-xs font-medium uppercase tracking-wide text-slate-400">{changeField(c)}</td>
                    <td className="td text-xs">
                      <div className="max-w-[460px]">
                        <div className="line-clamp-1 text-slate-500">Current: {c.old_preview ?? "-"}</div>
                        <div className="line-clamp-2 font-medium text-cyan-accent">Proposed: {c.new_preview ?? "-"}</div>
                        {c.review_note && <div className="mt-1 line-clamp-2 text-[11px] text-amber-100">{c.review_note}</div>}
                      </div>
                    </td>
                    <td className="td text-slate-400">{c.confidence != null ? c.confidence.toFixed(2) : "-"}</td>
                    <td className="td max-w-[160px] truncate text-[11px] text-slate-500" title={c.source_url}>
                      {changeSource(c)}
                    </td>
                    <td className="td whitespace-nowrap text-right">
                      <button className="text-xs text-emerald-300 hover:underline" onClick={() => decide(c.id, "approved")}>
                        approve
                      </button>
                      <button className="ml-3 text-xs text-rose-300 hover:underline" onClick={() => decide(c.id, "rejected")}>
                        reject
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          </>
        )}
      </div>

      {/* Newly discovered */}
      {discovered.length > 0 && (
        <div>
          <SectionTitle title={`Newly discovered (${discovered.length})`} subtitle="Quick review pass — does not block the catalog." />
          <Card className="soft-scroll flex max-h-[220px] flex-wrap gap-2 pr-1">
            {discovered.map((d) => (
              <span key={d.id} className="chip bg-ink-500 text-slate-300">
                {issuerCardLabel(d.issuer, cardName(d))}
                <button
                  className="ml-2 text-cyan-accent hover:underline"
                  onClick={async () => {
                    await api.reviewDiscovered(d.id);
                    load();
                  }}
                >
                  ✓
                </button>
              </span>
            ))}
          </Card>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Watchlist */}
        <ListManager
          title="Watchlist"
          subtitle="Manual backstop — always tracked."
          items={watchlist}
          onAdd={(issuer, product_name, extra) =>
            run(() => api.addWatchlist({ issuer, product_name, priority: extra }), "Watchlist add")
          }
          onRemove={(id) => run(() => api.removeWatchlist(id), "Watchlist remove")}
          extraLabel="priority"
          running={running}
        />
        {/* Blacklist */}
        <ListManager
          title="Blacklist"
          subtitle="Excluded from catalog, scoring, pipeline & discovery."
          items={blacklist}
          onAdd={(issuer, product_name, _e, reason) =>
            run(() => api.addBlacklist({ issuer, product_name, reason }), "Blacklist add")
          }
          onRemove={(id) => run(() => api.removeBlacklist(id), "Blacklist remove")}
          reasonField
          running={running}
        />
      </div>

      {/* Valuations */}
      <div>
        <SectionTitle title="Point valuations (cpp)" subtitle="Your override wins when set; otherwise the scraped value applies." />
        {valuations.length === 0 ? (
          <Card className="text-sm text-slate-500">No valuations yet — Refresh offers backfills them, or add manually below.</Card>
        ) : (
          <>
          <div className="soft-scroll max-h-[420px] space-y-2 pr-1 md:hidden">
            {valuations.map((v) => (
              <ValuationCard key={v.id} v={v} onSaved={load} flash={flash} />
            ))}
          </div>
          <Card className="soft-scroll hidden max-h-[460px] overflow-x-auto p-0 md:block">
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Currency</th>
                  <th className="th">Scraped</th>
                  <th className="th">Override</th>
                  <th className="th">Effective</th>
                  <th className="th"></th>
                </tr>
              </thead>
              <tbody>
                {valuations.map((v) => (
                  <ValuationRow key={v.id} v={v} onSaved={load} flash={flash} />
                ))}
              </tbody>
            </table>
          </Card>
          </>
        )}
        <AddValuation onSaved={load} flash={flash} />
      </div>

      {/* Sources */}
      {sources && (
        <Card>
          <div className="text-sm font-medium text-slate-100 mb-1">Sources</div>
          <div className="text-[11px] text-slate-500 mb-2">Discovery scope: {sources.discovery_issuers?.join(", ")}</div>
          <div className="soft-scroll max-h-[360px] space-y-1 pr-1">
            {(sources.records ?? []).map((s: any) => (
              <SourceRow key={s.id} source={s} onSaved={load} flash={flash} />
            ))}
          </div>
          <AddSource onSaved={load} flash={flash} />
        </Card>
      )}
    </div>
  );
}

function ListManager({
  title,
  subtitle,
  items,
  onAdd,
  onRemove,
  extraLabel,
  reasonField,
  running,
}: {
  title: string;
  subtitle: string;
  items: any[];
  onAdd: (issuer: string, product: string, extra?: boolean, reason?: string) => void;
  onRemove: (id: number) => void;
  extraLabel?: string;
  reasonField?: boolean;
  running: boolean;
}) {
  const [issuer, setIssuer] = useState("");
  const [product, setProduct] = useState("");
  const [extra, setExtra] = useState(false);
  const [reason, setReason] = useState("");

  return (
    <div>
      <SectionTitle title={`${title} (${items.length})`} subtitle={subtitle} />
      <Card className="space-y-2">
        <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
          <input className="input" placeholder="Issuer" value={issuer} onChange={(e) => setIssuer(e.target.value)} />
          <input className="input col-span-2 sm:flex-1" placeholder="Product name" value={product} onChange={(e) => setProduct(e.target.value)} />
          {extraLabel && (
            <label className="flex items-center gap-1 text-xs text-slate-400">
              <input type="checkbox" checked={extra} onChange={(e) => setExtra(e.target.checked)} />
              {extraLabel}
            </label>
          )}
          {reasonField && <input className="input" placeholder="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />}
          <button
            className="btn-ghost justify-center"
            disabled={running || !issuer || !product}
            onClick={() => {
              onAdd(issuer, product, extra, reason);
              setIssuer("");
              setProduct("");
              setExtra(false);
              setReason("");
            }}
          >
            Add
          </button>
        </div>
        <ul className="soft-scroll max-h-[260px] divide-y divide-ink-500/40 pr-1">
          {items.map((it) => (
            <li key={it.id} className="flex items-start justify-between gap-3 py-1.5 text-sm">
              <span className="min-w-0 break-words text-slate-200">
                {issuerCardLabel(it.issuer, cardName(it))}
                {it.priority && <span className="ml-2 chip bg-cyan-accent/15 text-cyan-accent">priority</span>}
                {it.reason && <span className="ml-2 text-[11px] text-slate-500">({it.reason})</span>}
              </span>
              <button className="shrink-0 text-xs text-slate-500 hover:text-pink-accent" onClick={() => onRemove(it.id)}>
                remove
              </button>
            </li>
          ))}
          {items.length === 0 && <li className="py-1.5 text-sm text-slate-500">Empty.</li>}
        </ul>
      </Card>
    </div>
  );
}

function ValuationRow({ v, onSaved, flash }: { v: any; onSaved: () => void; flash: Flash }) {
  const [override, setOverride] = useState(v.cpp_override ?? "");
  const [busy, setBusy] = useState(false);
  return (
    <tr>
      <td className="td text-slate-200">{v.currency}</td>
      <td className="td tabular-nums text-slate-400">{v.cpp_scraped ?? "—"}</td>
      <td className="td">
        <input
          className="input max-w-[90px]"
          type="number"
          step="0.1"
          value={override}
          onChange={(e) => setOverride(e.target.value)}
        />
      </td>
      <td className="td font-semibold tabular-nums text-cyan-accent">{v.cpp_effective ?? "—"}</td>
      <td className="td text-right">
        <button
          className="text-xs text-cyan-accent hover:underline"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await api.upsertValuation({ currency: v.currency, cpp_override: override === "" ? null : Number(override) });
              flash("info", "Valuation override saved.");
              onSaved();
            } catch (e: any) {
              flash("error", e.message);
            } finally {
              setBusy(false);
            }
          }}
        >
          save
        </button>
      </td>
    </tr>
  );
}

function ValuationCard({ v, onSaved, flash }: { v: any; onSaved: () => void; flash: Flash }) {
  const [override, setOverride] = useState(v.cpp_override ?? "");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await api.upsertValuation({ currency: v.currency, cpp_override: override === "" ? null : Number(override) });
      flash("info", "Valuation override saved.");
      onSaved();
    } catch (e: any) {
      flash("error", e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium text-slate-100">{v.currency}</div>
          <div className="text-[11px] text-slate-500">scraped {v.cpp_scraped ?? "-"}</div>
        </div>
        <div className="shrink-0 text-right">
          <div className="font-semibold tabular-nums text-cyan-accent">{v.cpp_effective ?? "-"}</div>
          <div className="text-[10px] text-slate-500">effective</div>
        </div>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
        <input className="input min-w-0" type="number" step="0.1" placeholder="Override cpp" value={override} onChange={(e) => setOverride(e.target.value)} />
        <button className="btn-ghost justify-center" disabled={busy} onClick={save}>save</button>
      </div>
    </Card>
  );
}

function AddValuation({ onSaved, flash }: { onSaved: () => void; flash: Flash }) {
  const [currency, setCurrency] = useState("");
  const [cpp, setCpp] = useState("");
  return (
    <div className="mt-2 grid grid-cols-[minmax(0,1fr)_88px] gap-2 sm:flex">
      <input className="input sm:max-w-xs" placeholder="Currency" value={currency} onChange={(e) => setCurrency(e.target.value)} />
      <input className="input" type="number" step="0.1" placeholder="cpp" value={cpp} onChange={(e) => setCpp(e.target.value)} />
      <button
        className="btn-ghost col-span-2 justify-center sm:w-auto"
        disabled={!currency || !cpp}
        onClick={async () => {
          try {
            await api.upsertValuation({ currency, cpp_scraped: Number(cpp) });
            flash("info", "Valuation added.");
            setCurrency("");
            setCpp("");
            onSaved();
          } catch (e: any) {
            flash("error", e.message);
          }
        }}
      >
        Add valuation
      </button>
    </div>
  );
}

function SourceRow({ source, onSaved, flash }: { source: any; onSaved: () => void; flash: Flash }) {
  const [active, setActive] = useState(Boolean(source.active));
  const [priority, setPriority] = useState(String(source.priority ?? 3));
  const [busy, setBusy] = useState(false);

  const save = async (nextActive = active) => {
    setBusy(true);
    try {
      await api.updateSource(source.id, { active: nextActive, priority: Number(priority) || 3 });
      flash("info", "Source updated.");
      onSaved();
    } catch (e: any) {
      flash("error", e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-2 rounded-md border border-ink-500/50 bg-ink-800/60 px-2 py-1.5 text-xs sm:grid-cols-[auto_58px_minmax(120px,auto)_1fr_auto] sm:items-center">
      <input
        type="checkbox"
        checked={active}
        disabled={busy}
        onChange={(e) => {
          setActive(e.target.checked);
          save(e.target.checked);
        }}
      />
      <input
        className="input max-w-[58px] py-1 text-xs"
        type="number"
        min={1}
        max={5}
        value={priority}
        onChange={(e) => setPriority(e.target.value)}
        onBlur={() => save()}
      />
      <span className="min-w-0 text-slate-300">{source.name}</span>
      <span className="flex-1 truncate text-slate-500" title={source.url}>{source.url}</span>
      <button
        className="text-left text-xs text-slate-500 hover:text-pink-accent sm:text-right"
        disabled={busy}
        onClick={async () => {
          await api.deleteSource(source.id);
          flash("info", "Source disabled.");
          onSaved();
        }}
      >
        disable
      </button>
    </div>
  );
}

function AddSource({ onSaved, flash }: { onSaved: () => void; flash: Flash }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <div className="mt-2 grid gap-2 sm:flex">
      <input className="input sm:max-w-[160px]" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
      <input className="input sm:flex-1" placeholder="https://..." value={url} onChange={(e) => setUrl(e.target.value)} />
      <button
        className="btn-ghost justify-center"
        disabled={busy || !name || !url}
        onClick={async () => {
          setBusy(true);
          try {
            await api.createSource({ name, url, kind: "offer", active: true, priority: 3 });
            flash("info", "Source added.");
            setName("");
            setUrl("");
            onSaved();
          } catch (e: any) {
            flash("error", e.message);
          } finally {
            setBusy(false);
          }
        }}
      >
        Add source
      </button>
    </div>
  );
}
