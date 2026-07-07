import { useEffect, useRef, useState } from "react";
import type { RunStatus } from "../lib/api";

export const TABS = ["Dashboard", "Household", "Profiles", "Card Plan", "Pipeline", "Redemption", "Card Universe"] as const;
export type Tab = (typeof TABS)[number];

export function TopNav({
  tab,
  onTab,
  user,
  onChangeProfile,
  runStatus,
  running,
  onDiscover,
  onRefresh,
  pendingReviewCount = 0,
  theme,
  onTheme,
}: {
  tab: Tab;
  onTab: (t: Tab) => void;
  user: string;
  onChangeProfile: () => void;
  runStatus: RunStatus | null;
  running: boolean;
  onDiscover: () => void;
  onRefresh: (options?: Record<string, any>) => void;
  pendingReviewCount?: number;
  theme: "light" | "dark";
  onTheme: (theme: "light" | "dark") => void;
}) {
  const [runOpen, setRunOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const closeIfOutsideMenu = (e: PointerEvent) => {
      const target = e.target;
      if (!(target instanceof Element)) return;
      if (!target.closest("[data-top-menu-root]")) {
        setRunOpen(false);
        setSettingsOpen(false);
      }
    };
    const closeOnEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setRunOpen(false);
        setSettingsOpen(false);
      }
    };
    document.addEventListener("pointerdown", closeIfOutsideMenu);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeIfOutsideMenu);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, []);

  const llmOk = runStatus?.llm_available;
  const webOk = runStatus?.web_search_enabled;

  return (
    <header ref={ref} className="sticky top-0 z-40 border-b border-ink-400/50 bg-ink-900/85 backdrop-blur">
      <div className="mx-auto flex max-w-7xl flex-col gap-2 px-3 py-2 sm:px-4 md:flex-row md:items-center md:gap-4 md:py-2.5">
        <div className="flex min-w-0 items-center gap-2 md:w-auto md:shrink-0 md:pr-3">
          <div className="flex shrink-0 items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-sm bg-gradient-to-br from-cyan-accent to-pink-accent" />
            <span className="font-semibold tracking-tight text-slate-100">WEwards</span>
          </div>
          <div className="ml-auto flex items-center gap-2 md:hidden">
            <TopControls
              user={user}
              onChangeProfile={onChangeProfile}
              runOpen={runOpen}
              setRunOpen={setRunOpen}
              running={running}
              llmOk={llmOk}
              webOk={webOk}
              onDiscover={onDiscover}
              onRefresh={onRefresh}
              runStatus={runStatus}
              settingsOpen={settingsOpen}
              setSettingsOpen={setSettingsOpen}
              onSettingsTab={onTab}
              pendingReviewCount={pendingReviewCount}
              theme={theme}
              onTheme={onTheme}
            />
          </div>
        </div>

        <nav className="scrollbar-none hidden max-w-full items-center gap-1 overflow-x-auto pb-1 md:flex md:flex-1 md:pb-0">
          {TABS.filter((t) => t !== "Card Universe").map((t) => (
            <button
              key={t}
              onClick={() => onTab(t)}
              className={`shrink-0 rounded-lg px-3 py-1.5 text-sm font-medium whitespace-nowrap transition-colors ${
                tab === t
                  ? "bg-ink-600 text-cyan-accent"
                  : "text-slate-400 hover:text-slate-200 hover:bg-ink-700"
              }`}
            >
              {t}
            </button>
          ))}
        </nav>

        <div className="hidden items-center gap-2 md:ml-auto md:flex">
          <TopControls
            user={user}
            onChangeProfile={onChangeProfile}
            runOpen={runOpen}
            setRunOpen={setRunOpen}
            running={running}
            llmOk={llmOk}
            webOk={webOk}
            onDiscover={onDiscover}
            onRefresh={onRefresh}
            runStatus={runStatus}
            settingsOpen={settingsOpen}
            setSettingsOpen={setSettingsOpen}
            onSettingsTab={onTab}
            pendingReviewCount={pendingReviewCount}
            theme={theme}
            onTheme={onTheme}
          />
        </div>
      </div>
    </header>
  );
}

function TopControls({
  user,
  onChangeProfile,
  runOpen,
  setRunOpen,
  running,
  llmOk,
  webOk,
  onDiscover,
  onRefresh,
  runStatus,
  settingsOpen,
  setSettingsOpen,
  onSettingsTab,
  pendingReviewCount,
  theme,
  onTheme,
}: {
  user: string;
  onChangeProfile: () => void;
  runOpen: boolean;
  setRunOpen: (updater: boolean | ((open: boolean) => boolean)) => void;
  running: boolean;
  llmOk: boolean | undefined;
  webOk: boolean | undefined;
  onDiscover: () => void;
  onRefresh: (options?: Record<string, any>) => void;
  runStatus: RunStatus | null;
  settingsOpen: boolean;
  setSettingsOpen: (updater: boolean | ((open: boolean) => boolean)) => void;
  onSettingsTab: (tab: Tab) => void;
  pendingReviewCount: number;
  theme: "light" | "dark";
  onTheme: (theme: "light" | "dark") => void;
}) {
  return (
    <>
      <div className="relative" data-top-menu-root="run">
        <button
          className="btn-ghost px-2.5 py-1 text-xs sm:px-3 sm:py-1.5 sm:text-sm"
          onClick={() => {
            setSettingsOpen(false);
            setRunOpen((o) => !o);
          }}
          disabled={running}
        >
          {running ? (
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-cyan-accent/40 border-t-cyan-accent" />
          ) : (
            <span>Run</span>
          )}
          <span className="hidden text-[10px] opacity-70 sm:inline">v</span>
        </button>
        {runOpen && (
          <div className="absolute right-0 mt-1 w-[calc(100vw-1.5rem)] max-w-80 rounded-lg border border-ink-400/60 bg-ink-700 p-1 shadow-xl sm:w-64">
            <RunItem
              label="Discover cards"
              hint="Enumerate the card universe (run occasionally)"
              disabled={!llmOk || running}
              onClick={() => {
                setRunOpen(false);
                onDiscover();
              }}
            />
            <RunItem
              label="Refresh"
              hint="Static/cache first; cards still missing info escalate to rendered + web search automatically. Review auto-verifies."
              disabled={!llmOk || running}
              onClick={() => {
                setRunOpen(false);
                onRefresh({
                  limit: null,
                  only_stale: true,
                  include_incomplete: true,
                  use_rendered_fallback: true,
                  use_web_search: true,
                  web_fallback_limit: 12,
                  refresh_valuations: true,
                });
              }}
            />
            <RunItem
              label="Refresh valuations"
              hint="Update missing sourced cpp values"
              disabled={!llmOk || !webOk || running}
              onClick={() => {
                setRunOpen(false);
                onRefresh({ valuations_only: true, refresh_valuations: true });
              }}
            />
            <RunItem label="Award availability" hint="Use the Redemption tab; seats.aero is optional" disabled />
            {!llmOk && (
              <p className="px-2 py-1.5 text-[11px] text-amber-300/80">
                Set ANTHROPIC_API_KEY in .env to enable discovery / refresh.
              </p>
            )}
            {llmOk && !webOk && (
              <p className="px-2 py-1.5 text-[11px] text-amber-300/80">
                Enable WEB_SEARCH_ENABLED for deep refresh and sourced valuations.
              </p>
            )}
          </div>
        )}
      </div>

      <div className="relative" data-top-menu-root="settings">
        <button
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-ink-400/60 bg-ink-800 text-slate-300 transition-colors hover:bg-ink-700 hover:text-slate-100"
          onClick={() => {
            setRunOpen(false);
            setSettingsOpen((open) => !open);
          }}
          aria-label="Settings"
        >
          <GearIcon />
          {pendingReviewCount > 0 && (
            <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full border border-ink-900 bg-rose-500 px-1 text-[9px] font-semibold leading-none text-white">
              {pendingReviewCount > 9 ? "9+" : pendingReviewCount}
            </span>
          )}
        </button>
        {settingsOpen && (
          <div className="settings-menu absolute right-0 mt-1 w-[calc(100vw-1.5rem)] max-w-[14rem] rounded-lg border border-ink-400/60 bg-ink-700 p-1 shadow-xl sm:w-56">
            <div className="flex items-center justify-between gap-3 rounded-md px-2 py-2">
              <span className="text-sm text-slate-100">Dark mode</span>
              <button
                type="button"
                className={`settings-toggle relative h-5 w-9 overflow-hidden rounded-full border transition-colors ${theme === "dark" ? "is-dark" : ""}`}
                aria-pressed={theme === "dark"}
                onClick={() => onTheme(theme === "dark" ? "light" : "dark")}
              >
                <span
                  className={`absolute left-0.5 top-0.5 h-3.5 w-3.5 rounded-full bg-slate-100 transition-transform ${
                    theme === "dark" ? "translate-x-4" : "translate-x-0"
                  }`}
                />
              </button>
            </div>
            <button
              className="settings-menu-item w-full rounded-md px-2 py-2 text-left"
              onClick={() => {
                setSettingsOpen(false);
                onChangeProfile();
              }}
            >
              <div className="text-sm text-slate-100">Change profile</div>
              <div className="text-[11px] text-slate-500">Current: {user}</div>
            </button>
            <div className="my-1 border-t border-ink-400/60" />
            <button
              className="settings-menu-item w-full rounded-md px-2 py-2 text-left text-sm text-slate-100"
              onClick={() => {
                setSettingsOpen(false);
                onSettingsTab("Card Universe");
              }}
            >
              Card Universe
            </button>
          </div>
        )}
      </div>

      <StatusPill runStatus={runStatus} />
    </>
  );
}

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.1a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.6 1Z" />
    </svg>
  );
}

function RunItem({
  label,
  hint,
  disabled,
  onClick,
}: {
  label: string;
  hint: string;
  disabled?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="settings-menu-item w-full rounded-md px-2 py-2 text-left disabled:opacity-40 disabled:cursor-not-allowed"
    >
      <div className="text-sm text-slate-100">{label}</div>
      <div className="text-[11px] text-slate-500">{hint}</div>
    </button>
  );
}

function StatusPill({ runStatus }: { runStatus: RunStatus | null }) {
  if (!runStatus) return null;
  const items = [
    { ok: runStatus.crypto_available, label: "enc" },
    { ok: runStatus.llm_available, label: "llm" },
  ];
  return (
    <div className="hidden md:flex items-center gap-1.5 rounded-lg border border-ink-400/60 bg-ink-800 px-2 py-1">
      {items.map((i) => (
        <span key={i.label} className="flex items-center gap-1 text-[10px] text-slate-400">
          <span className={`h-1.5 w-1.5 rounded-full ${i.ok ? "bg-emerald-400" : "bg-slate-600"}`} />
          {i.label}
        </span>
      ))}
    </div>
  );
}
