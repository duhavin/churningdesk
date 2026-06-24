import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { TopNav, type Tab } from "./components/TopNav";
import { api, type RefreshJob, type RunStatus } from "./lib/api";
import { Dashboard } from "./pages/Dashboard";
import { CardPlan } from "./pages/CardPlan";
import { Profiles } from "./pages/Profiles";
import { Pipeline } from "./pages/Pipeline";
import { Household } from "./pages/Household";
import { Redemption } from "./pages/Redemption";
import { CardUniverse } from "./pages/CardUniverse";
import { MobileProfile } from "./pages/MobileProfile";
import { MobileBenefits } from "./pages/MobileBenefits";

export type Flash = (kind: "info" | "warn" | "error", msg: string) => void;

const PHASE_LABELS: Record<string, string> = {
  idle: "Idle",
  starting: "Starting",
  static_parse: "Static parse",
  llm_batch: "LLM batch",
  rendered_fetch: "Rendered fallback",
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

const EXPECTED_REFRESH_NOTICE_CODES = new Set([
  "llm_schema_limit",
  "web_search_cooldown",
  "supplemental_search_cooldown",
  "web_search_stale_gate",
  "web_search_deferred",
  "web_search_no_result",
  "web_search_missing_real_cited_url",
  "crawl4ai_disabled",
]);

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function hasUnexpectedRefreshWarning(warnings: any[] | undefined): boolean {
  return Boolean(
    warnings?.some((warning) => {
      const code = typeof warning?.code === "string" ? warning.code : "";
      return !code || !EXPECTED_REFRESH_NOTICE_CODES.has(code);
    }),
  );
}

const MOBILE_TABS = ["Home", "My Cards", "Pipeline", "Benefits", "Travel"] as const;
const HOME_SEGMENTS = ["Dashboard", "Household"] as const;
const MY_CARDS_SEGMENTS = ["Profile", "Discover"] as const;
type MobileTab = (typeof MOBILE_TABS)[number];
type MobileIconName = "home" | "cards" | "pipeline" | "benefits" | "travel";
type ThemeMode = "light" | "dark";
const THEME_STORAGE_KEY = "churn-theme-v3";
const PROFILE_THEME_STORAGE_PREFIX = "churn-profile-theme-v1:";

function storedTheme(value: string | null): ThemeMode | null {
  return value === "dark" || value === "light" ? value : null;
}

function initialTheme(): ThemeMode {
  try {
    return storedTheme(window.localStorage.getItem(THEME_STORAGE_KEY)) ?? "light";
  } catch {
    return "light";
  }
}

function profileTheme(user: string): ThemeMode {
  try {
    return storedTheme(window.localStorage.getItem(`${PROFILE_THEME_STORAGE_PREFIX}${encodeURIComponent(user)}`)) ?? "light";
  } catch {
    return "light";
  }
}

const MOBILE_TAB_ICONS: Record<MobileTab, MobileIconName> = {
  Home: "home",
  "My Cards": "cards",
  Pipeline: "pipeline",
  Benefits: "benefits",
  Travel: "travel",
};

function MobileIcon({ name }: { name: MobileIconName }) {
  if (name === "home") {
    return (
      <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <path d="m3 11 9-8 9 8" />
        <path d="M5 10v10h14V10" />
        <path d="M10 20v-6h4v6" />
      </svg>
    );
  }
  if (name === "cards") {
    return (
      <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <rect x="3" y="6" width="18" height="12" rx="2" />
        <path d="M3 10h18" />
        <path d="M7 15h4" />
      </svg>
    );
  }
  if (name === "pipeline") {
    return (
      <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <path d="M5 12h14" />
        <path d="m13 6 6 6-6 6" />
      </svg>
    );
  }
  if (name === "benefits") {
    return (
      <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <rect x="3" y="8" width="18" height="13" rx="2" />
        <path d="M12 8v13" />
        <path d="M3 12h18" />
        <path d="M12 8c-2.2 0-4-1.2-4-3a2 2 0 0 1 4 0" />
        <path d="M12 8c2.2 0 4-1.2 4-3a2 2 0 0 0-4 0" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M2 16 22 8" />
      <path d="m6 15-2 5 5-2" />
      <path d="m14 11 4 8 2-1-3-9" />
      <path d="m8 13-5-4 2-1 7 3" />
    </svg>
  );
}

function MobileBottomNav({
  active,
  onSelect,
}: {
  active: MobileTab | null;
  onSelect: (tab: MobileTab) => void;
}) {
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-ink-400/60 bg-ink-900/95 px-2 pb-[calc(env(safe-area-inset-bottom)+0.35rem)] pt-1.5 backdrop-blur md:hidden">
      <div className="mx-auto grid max-w-md grid-cols-5 gap-1">
        {MOBILE_TABS.map((item) => {
          const selected = active === item;
          return (
            <button
              key={item}
              className={`flex h-14 flex-col items-center justify-center rounded-xl text-[10px] font-medium transition-colors ${
                selected ? "bg-cyan-accent/12 text-cyan-accent" : "text-slate-500 hover:text-slate-200"
              }`}
              onClick={() => onSelect(item)}
            >
              <MobileIcon name={MOBILE_TAB_ICONS[item]} />
              <span className="mt-0.5 leading-none">{item}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}

function MobileSegment<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: readonly T[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="md:hidden">
      <div className="grid grid-cols-2 gap-1 rounded-xl border border-ink-400/60 bg-ink-800 p-1">
        {options.map((option) => (
          <button
            key={option}
            className={`rounded-lg px-3 py-2 text-sm font-semibold transition-colors ${
              value === option ? "bg-ink-600 text-cyan-accent" : "text-slate-400"
            }`}
            onClick={() => onChange(option)}
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  );
}

function MobilePanel({ children }: { children: ReactNode }) {
  return <div className="space-y-4">{children}</div>;
}

function WelcomeScreen({
  users,
  loading,
  onSelect,
  onAddProfile,
}: {
  users: string[];
  loading: boolean;
  onSelect: (user: string) => void;
  onAddProfile: () => void;
}) {
  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <section className="welcome-shell w-full max-w-md text-center">
        <div className="welcome-step">
          <div className="mx-auto mb-5 h-10 w-10 rounded-xl border border-cyan-accent/30 bg-cyan-accent/10 p-2">
            <div className="h-full w-full rounded-md bg-gradient-to-br from-cyan-accent to-pink-accent" />
          </div>
          <h1 className="text-3xl font-semibold tracking-tight text-slate-100">Welcome to Churn</h1>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            Household card strategy, points, benefits, and next moves in one place.
          </p>
        </div>

        <div className="welcome-step welcome-step-2 mt-8">
          <div className="text-xs font-semibold uppercase tracking-wide text-emerald-300">Select profile</div>
          <div className="mt-1 text-sm text-slate-400">Who is accessing the app?</div>
        </div>

        <div className="welcome-step welcome-step-3 mt-4 grid min-h-[64px] grid-cols-2 gap-2">
          {loading && users.length === 0 ? (
            <>
              <div className="h-[52px] rounded-lg border border-ink-400/60 bg-ink-800/70" />
              <div className="h-[52px] rounded-lg border border-ink-400/60 bg-ink-800/70" />
            </>
          ) : (
            users.map((name, index) => (
              <button
                key={name}
                className="welcome-profile-card group rounded-lg border border-ink-400/60 bg-ink-800/65 px-2.5 py-2 text-left shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:border-slate-500/60 hover:bg-ink-700/80 hover:shadow-md"
                style={{ animationDelay: `${860 + index * 110}ms` }}
                onClick={() => onSelect(name)}
                aria-label={`Open ${name} profile`}
              >
                <div className="flex min-w-0 items-center gap-2">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-ink-400/70 bg-ink-900/60 text-xs font-semibold text-slate-200">
                    {name.slice(0, 1)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm font-semibold leading-tight text-slate-100">{name}</span>
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-ink-400/60 text-base leading-none text-slate-400 transition-colors group-hover:text-slate-100">
                    &rsaquo;
                  </span>
                </div>
              </button>
            ))
          )}
        </div>

        <button
          type="button"
          className="welcome-step welcome-step-4 mt-4 text-xs font-medium text-slate-500 underline-offset-4 hover:text-cyan-accent hover:underline"
          onClick={onAddProfile}
        >
          Add household profile
        </button>
      </section>
    </main>
  );
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
  const statusText = job.phase === "failed"
    ? firstError || "Refresh failed before returning a result."
    : job.current_product || (hasWarnings ? `${job.warnings.length} refresh notice${job.warnings.length === 1 ? "" : "s"}` : "Preparing selected cards");

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
  const [mobileTab, setMobileTab] = useState<MobileTab>("Home");
  const [homeTab, setHomeTab] = useState<"Dashboard" | "Household">("Dashboard");
  const [myCardsTab, setMyCardsTab] = useState<"Profile" | "Discover">("Profile");
  const [users, setUsers] = useState<string[]>([]);
  const [usersLoading, setUsersLoading] = useState(true);
  const [profileSelected, setProfileSelected] = useState(false);
  const [user, setUser] = useState<string>("");
  const [runStatus, setRunStatus] = useState<RunStatus | null>(null);
  const [pendingReviewCount, setPendingReviewCount] = useState(0);
  const [theme, setTheme] = useState<ThemeMode>(initialTheme);
  const [refreshJob, setRefreshJob] = useState<RefreshJob | null>(null);
  const [dismissedRefreshKey, setDismissedRefreshKey] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [toast, setToast] = useState<{ kind: string; msg: string } | null>(null);
  const [bump, setBump] = useState(0);
  const toastTimer = useRef<number | null>(null);

  useEffect(() => {
    api.users()
      .then(setUsers)
      .catch(() => setUsers([]))
      .finally(() => setUsersLoading(false));
    api.runStatus().then(setRunStatus).catch(() => {});
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("light", theme === "light");
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);

  useEffect(() => {
    api.proposedChanges("pending")
      .then((rows) => setPendingReviewCount(rows.length))
      .catch(() => setPendingReviewCount(0));
  }, [bump]);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const poll = async () => {
      let running = false;
      try {
        const job = await api.refreshStatus();
        if (alive) setRefreshJob(job);
        running = Boolean(job?.running);
      } catch {
        /* backend may not be ready during startup */
      }
      if (!alive) return;
      // Poll fast only while a refresh job is active; back off when idle and
      // pause entirely when the tab is hidden — the status endpoint is cheap
      // but there's no reason to hit it every 2s forever.
      const delay = running ? 2000 : document.hidden ? 60000 : 15000;
      timer = window.setTimeout(poll, delay);
    };
    poll();
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
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
      const notices = r.warnings?.length || 0;
      const deferred = num(r.scan?.web_search_deferred) || r.products_deferred_by_limit || 0;
      const needsData = num(r.scan?.cards_needs_data);
      const cardsResolved = num(r.scan?.cards_resolved);
      const cooldownSkipped = num(r.scan?.web_search_cooldown_skipped) + num(r.scan?.supplemental_search_cooldown_skipped);
      const renderedPages = num(r.scan?.rendered_pages);
      const checked = r.refreshed_count ?? r.products_checked ?? r.results.length;
      const parts = [
        `Refresh checked ${checked} card(s)`,
        `${committed} committed`,
        `${proposed} queued`,
        `${cardsResolved} resolved`,
      ];
      if (renderedPages) parts.push(`${renderedPages} rendered`);
      if (needsData) parts.push(`${needsData} need data`);
      if (r.peaks_filled) parts.push(`${r.peaks_filled} peak(s) filled`);
      if (r.valuations_added) parts.push(`${r.valuations_added} valuation(s) added`);
      if (deferred) parts.push(`${deferred} deferred`);
      if (cooldownSkipped) parts.push(`${cooldownSkipped} cooldown skip(s)`);
      if (notices) parts.push(`${notices} notice${notices === 1 ? "" : "s"}`);
      flash(
        hasUnexpectedRefreshWarning(r.warnings) ? "warn" : "info",
        `${parts.join(", ")}.`,
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
  const showMobileConfig = tab === "Card Universe";
  const selectMobileTab = (next: MobileTab) => {
    setMobileTab(next);
    if (showMobileConfig) setTab("Dashboard");
  };

  const selectProfile = (nextUser: string) => {
    setTheme(profileTheme(nextUser));
    setUser(nextUser);
    setProfileSelected(true);
    setTab("Dashboard");
    setMobileTab("Home");
    setHomeTab("Dashboard");
    setMyCardsTab("Profile");
  };

  const updateTheme = (nextTheme: ThemeMode) => {
    setTheme(nextTheme);
    if (!profileSelected || !user) return;
    try {
      window.localStorage.setItem(`${PROFILE_THEME_STORAGE_PREFIX}${encodeURIComponent(user)}`, nextTheme);
    } catch {
      /* ignore unavailable storage */
    }
  };

  const changeProfile = () => {
    setProfileSelected(false);
    setUser("");
    setTab("Dashboard");
    setMobileTab("Home");
    setHomeTab("Dashboard");
    setMyCardsTab("Profile");
  };

  const desktopPage = () => {
    if (tab === "Dashboard") return <Dashboard {...pageProps} />;
    if (tab === "Card Plan") return <CardPlan {...pageProps} />;
    if (tab === "Profiles") return <Profiles {...pageProps} />;
    if (tab === "Pipeline") return <Pipeline {...pageProps} />;
    if (tab === "Household") return <Household {...pageProps} />;
    if (tab === "Redemption") return <Redemption {...pageProps} />;
    return <CardUniverse {...pageProps} />;
  };

  const mobilePage = () => {
    if (showMobileConfig) return <CardUniverse {...pageProps} />;
    if (mobileTab === "Home") {
      return (
        <MobilePanel>
          <MobileSegment value={homeTab} options={HOME_SEGMENTS} onChange={(value) => setHomeTab(value)} />
          {homeTab === "Dashboard" ? (
            <Dashboard
              {...pageProps}
              onOpenBenefit={(targetUser) => {
                if (targetUser !== user) return;
                setMobileTab("Benefits");
              }}
            />
          ) : (
            <Household {...pageProps} />
          )}
        </MobilePanel>
      );
    }
    if (mobileTab === "My Cards") {
      return (
        <MobilePanel>
          <MobileSegment value={myCardsTab} options={MY_CARDS_SEGMENTS} onChange={(value) => setMyCardsTab(value)} />
          {myCardsTab === "Profile" ? <MobileProfile {...pageProps} /> : <CardPlan {...pageProps} />}
        </MobilePanel>
      );
    }
    if (mobileTab === "Pipeline") return <Pipeline {...pageProps} />;
    if (mobileTab === "Benefits") return <MobileBenefits {...pageProps} />;
    return <Redemption {...pageProps} />;
  };

  if (!profileSelected || !user) {
    return (
      <div className="min-h-screen">
        <WelcomeScreen
          users={users}
          loading={usersLoading}
          onSelect={selectProfile}
          onAddProfile={() =>
            flash("info", "Adding household profiles needs a roster/data-model update before it can be enabled safely.")
          }
        />
        {toast && (
          <div className="fixed inset-x-3 bottom-4 z-50 sm:inset-x-auto sm:right-4 sm:max-w-md">
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

  return (
    <div className="min-h-screen">
      <TopNav
        tab={tab}
        onTab={setTab}
        user={user}
        onChangeProfile={changeProfile}
        runStatus={runStatus}
        running={anyRunActive}
        onDiscover={doDiscover}
        onRefresh={doRefresh}
        pendingReviewCount={pendingReviewCount}
        theme={theme}
        onTheme={updateTheme}
      />
      <RefreshProgressBanner
        job={visibleRefreshJob}
        onDismiss={() => setDismissedRefreshKey(refreshKey || "failed")}
      />

      <main className="mx-auto max-w-7xl px-3 pb-24 pt-4 sm:px-4 sm:py-6 md:pb-6">
        <div className="mobile-shell md:hidden">{mobilePage()}</div>
        <div className="hidden md:block">{desktopPage()}</div>
      </main>

      {user && <MobileBottomNav active={showMobileConfig ? null : mobileTab} onSelect={selectMobileTab} />}

      {toast && (
        <div className="fixed inset-x-3 bottom-24 z-50 sm:inset-x-auto sm:right-4 sm:bottom-4 sm:max-w-md">
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
