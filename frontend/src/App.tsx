import { useCallback, useEffect, useRef, useState } from "react";
import { TopNav, type Tab } from "./components/TopNav";
import { api, type RefreshJob, type RunStatus } from "./lib/api";
import { Dashboard } from "./pages/Dashboard";
import { CardPlan } from "./pages/CardPlan";
import { Profiles } from "./pages/Profiles";
import { Pipeline } from "./pages/Pipeline";
import { Household } from "./pages/Household";
import { CardUniverse } from "./pages/CardUniverse";

export type Flash = (kind: "info" | "warn" | "error", msg: string) => void;

const PHASE_LABELS: Record<string, string> = {
  idle: "Idle",
  starting: "Starting",
  static_parse: "Static parse",
  llm_batch: "LLM batch",
  web_search_batch: "Web fallback",
  complete: "Complete",
  failed: "Failed",
};

function phaseLabel(phase: string | null | undefined) {
  if (!phase) return "Starting";
  return PHASE_LABELS[phase] ?? phase.replace(/_/g, " ");
}

function elapsedLabel(startedAt: string | null | undefined) {
  if (!startedAt) return null;
  const started = new Date(startedAt);
  if (Number.isNaN(started.getTime())) return null;
  const seconds = Math.max(0, Math.floor((Date.now() - started.getTime()) / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes <= 0) return `${remainingSeconds}s`;
  return `${minutes}m ${remainingSeconds.toString().padStart(2, "0")}s`;
}

function RefreshProgressBanner({ job, onDismiss }: { job: RefreshJob | null; onDismiss: () => void }) {
  if (!job || (!job.running && job.phase !== "failed")) return null;

  const total = job.total || 0;
  const processed = job.processed || 0;
  const pct = total > 0 ? Math.min(100, Math.max(3, Math.round((processed / total) * 100))) : 8;
  const hasErrors = Boolean(job.errors?.length);
  const hasWarnings = Boolean(job.warnings?.length);
  const elapsed = elapsedLabel(job.started_at);
  const firstError = hasErrors ? job.errors[0]?.error || job.errors[0]?.detail || String(job.errors[0]) : null;
  const firstWarning = hasWarnings ? job.warnings[0]?.message || job.warnings[0]?.warning || String(job.warnings[0]) : null;
  const statusText = job.phase === "failed"
    ? firstError || "Refresh failed before returning a result."
    : firstWarning || job.current_product || "Preparing selected cards";

  return (
    <div className="sticky top-[96px] z-30 border-b border-cyan-accent/20 bg-ink-800/95 backdrop-blur md:top-[53px]">
      <div className="mx-auto max-w-7xl px-3 py-2 sm:px-4">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm text-slate-100">
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${
                  job.phase === "failed" ? "bg-rose-400" : "animate-pulse bg-cyan-accent"
                }`}
              />
              <span className="font-medium">Offer refresh</span>
              <span className="text-slate-500">/</span>
              <span className="text-cyan-100">{phaseLabel(job.phase)}</span>
            </div>
            <div className="mt-0.5 truncate text-xs text-slate-400">
              {statusText}
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs text-slate-300">
            <span className="rounded-md border border-ink-400/70 bg-ink-900 px-2 py-1">
              {processed}/{total || "?"} cards
            </span>
            <span className="rounded-md border border-emerald-500/30 bg-emerald-950/30 px-2 py-1 text-emerald-100">
              {job.committed || 0} committed
            </span>
            <span className="rounded-md border border-amber-500/30 bg-amber-950/30 px-2 py-1 text-amber-100">
              {job.proposed || 0} queued
            </span>
            {elapsed && (
              <span className="rounded-md border border-ink-400/70 bg-ink-900 px-2 py-1 text-slate-300">
                {elapsed}
              </span>
            )}
            {hasErrors && (
              <span className="rounded-md border border-rose-500/40 bg-rose-950/50 px-2 py-1 text-rose-100">
                {job.errors.length} error{job.errors.length === 1 ? "" : "s"}
              </span>
            )}
            {hasWarnings && (
              <span className="rounded-md border border-amber-500/40 bg-amber-950/50 px-2 py-1 text-amber-100">
                {job.warnings.length} warning{job.warnings.length === 1 ? "" : "s"}
              </span>
            )}
            {job.phase === "failed" && (
              <button
                className="rounded-md border border-ink-400/70 bg-ink-900 px-2 py-1 text-slate-300 hover:text-slate-100"
                onClick={onDismiss}
              >
                Dismiss
              </button>
            )}
          </div>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-900">
          <div
            className={`h-full rounded-full ${job.phase === "failed" ? "bg-rose-400" : "bg-cyan-accent"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>("Dashboard");
  const [users, setUsers] = useState<string[]>([]);
  const [user, setUser] = useState<string>("");
  const [runStatus, setRunStatus] = useState<RunStatus | null>(null);
  const [refreshJob, setRefreshJob] = useState<RefreshJob | null>(null);
  const [dismissedRefreshKey, setDismissedRefreshKey] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [toast, setToast] = useState<{ kind: string; msg: string } | null>(null);
  const [bump, setBump] = useState(0);
  const toastTimer = useRef<number | null>(null);

  useEffect(() => {
    api.users().then((us) => {
      setUsers(us);
      setUser((u) => u || us[0] || "");
    });
    api.runStatus().then(setRunStatus).catch(() => {});
  }, []);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const job = await api.refreshStatus();
        if (alive) setRefreshJob(job);
      } catch {
        /* backend may not be ready during startup */
      }
    };
    poll();
    const timer = window.setInterval(poll, 2000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (refreshJob?.running && dismissedRefreshKey) {
      setDismissedRefreshKey(null);
    }
  }, [dismissedRefreshKey, refreshJob?.running]);

  const flash: Flash = useCallback((kind, msg) => {
    setToast({ kind, msg });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 7000);
  }, []);

  const doDiscover = useCallback(async () => {
    setRunning(true);
    try {
      const r = await api.runDiscover();
      const msg = `Discovery: enumerated ${r.discovered_count}, added ${r.added_count} new card(s).`;
      flash(r.web_error ? "warn" : "info", r.web_error ? `${msg} Web research issue: ${r.web_error}` : msg);
      setBump((b) => b + 1);
    } catch (e: any) {
      flash("error", e.message);
    } finally {
      setRunning(false);
    }
  }, [flash]);

  const waitForRefresh = useCallback(async () => {
    const deadline = Date.now() + 10 * 60 * 1000;
    let lastError: unknown = null;
    while (Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
      try {
        const job = await api.refreshStatus();
        setRefreshJob(job);
        if (!job.running) return job.result ?? job;
      } catch (e) {
        lastError = e;
      }
    }
    throw new Error(lastError instanceof Error ? `Refresh status timed out: ${lastError.message}` : "Refresh status timed out.");
  }, []);

  const doRefresh = useCallback(async (options: Record<string, any> = {}) => {
    setRunning(true);
    try {
      const started = await api.runRefresh({ limit: null, only_stale: true, background: true, ...options });
      if (started?.job) setRefreshJob(started.job);
      const r = started?.status === "started" || started?.status === "already_running"
        ? await waitForRefresh()
        : started;
      if (!r?.results) {
        throw new Error(r?.errors?.[0]?.error ?? "Refresh did not return a result.");
      }
      if (r.scan?.mode === "valuations_only") {
        flash("info", `Valuations refreshed: ${r.valuations_added ?? 0} sourced value(s) added.`);
        setBump((b) => b + 1);
        return;
      }
      const committed = r.results.reduce((a: number, x: any) => a + (x.result.committed?.length || 0), 0);
      const proposed = r.results.reduce((a: number, x: any) => a + (x.result.proposed?.length || 0), 0);
      const warnings = r.warnings?.length || 0;
      const deferred = r.scan?.web_search_deferred || r.products_deferred_by_limit || 0;
      flash(
        warnings || deferred ? "warn" : "info",
        `Refreshed ${r.refreshed_count} card(s): ${committed} committed, ${proposed} queued, ${r.peaks_filled ?? 0} peak(s) filled, ${r.valuations_added ?? 0} valuation(s) added${deferred ? `, ${deferred} deferred` : ""}${warnings ? `, ${warnings} warning(s)` : ""}.`,
      );
      setBump((b) => b + 1);
    } catch (e: any) {
      flash("error", e.message);
    } finally {
      setRunning(false);
    }
  }, [flash, waitForRefresh]);

  const pageProps = { user, bump, flash };
  const anyRunActive = running || Boolean(refreshJob?.running);
  const refreshKey = refreshJob?.started_at || refreshJob?.finished_at || null;
  const visibleRefreshJob =
    refreshJob?.phase === "failed" && refreshKey && dismissedRefreshKey === refreshKey
      ? null
      : refreshJob;

  return (
    <div className="min-h-screen">
      <TopNav
        tab={tab}
        onTab={setTab}
        user={user}
        users={users}
        onUser={setUser}
        runStatus={runStatus}
        running={anyRunActive}
        onDiscover={doDiscover}
        onRefresh={doRefresh}
      />
      <RefreshProgressBanner
        job={visibleRefreshJob}
        onDismiss={() => setDismissedRefreshKey(refreshKey || "failed")}
      />

      <main className="mx-auto max-w-7xl px-3 py-4 sm:px-4 sm:py-6">
        {!user ? (
          <div className="text-slate-400">Loading…</div>
        ) : tab === "Dashboard" ? (
          <Dashboard {...pageProps} />
        ) : tab === "Card Plan" ? (
          <CardPlan {...pageProps} />
        ) : tab === "Profiles" ? (
          <Profiles {...pageProps} />
        ) : tab === "Pipeline" ? (
          <Pipeline {...pageProps} />
        ) : tab === "Household" ? (
          <Household {...pageProps} />
        ) : (
          <CardUniverse {...pageProps} />
        )}
      </main>

      {toast && (
        <div className="fixed inset-x-3 bottom-3 z-50 sm:inset-x-auto sm:right-4 sm:bottom-4 sm:max-w-md">
          <div
            className={`rounded-lg border px-4 py-3 text-sm shadow-xl ${
              toast.kind === "error"
                ? "border-rose-500/50 bg-rose-950/90 text-rose-100"
                : toast.kind === "warn"
                  ? "border-amber-500/50 bg-amber-950/90 text-amber-100"
                  : "border-cyan-accent/40 bg-ink-700/95 text-cyan-100"
            }`}
          >
            {toast.msg}
          </div>
        </div>
      )}
    </div>
  );
}
