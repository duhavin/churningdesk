import { useEffect, useRef, useState } from "react";
import type { RunStatus } from "../lib/api";

export const TABS = ["Dashboard", "Household", "Profiles", "Card Plan", "Pipeline", "Card Universe"] as const;
export type Tab = (typeof TABS)[number];

export function TopNav({
  tab,
  onTab,
  user,
  users,
  onUser,
  runStatus,
  running,
  onDiscover,
  onRefresh,
}: {
  tab: Tab;
  onTab: (t: Tab) => void;
  user: string;
  users: string[];
  onUser: (u: string) => void;
  runStatus: RunStatus | null;
  running: boolean;
  onDiscover: () => void;
  onRefresh: (options?: Record<string, any>) => void;
}) {
  const [runOpen, setRunOpen] = useState(false);
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setRunOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const llmOk = runStatus?.llm_available;
  const webOk = runStatus?.web_search_enabled;

  return (
    <header ref={ref} className="sticky top-0 z-40 border-b border-ink-400/50 bg-ink-900/85 backdrop-blur">
      <div className="mx-auto flex max-w-7xl flex-col gap-2 px-3 py-2 sm:px-4 md:flex-row md:items-center md:gap-4 md:py-2.5">
        <div className="flex min-w-0 items-center gap-2 md:w-auto md:shrink-0 md:pr-3">
          <div className="flex shrink-0 items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-sm bg-gradient-to-br from-cyan-accent to-pink-accent" />
            <span className="font-semibold tracking-tight text-slate-100">Churn</span>
          </div>
          <div className="ml-auto flex items-center gap-2 md:hidden">
            <TopControls
              users={users}
              user={user}
              onUser={onUser}
              runOpen={runOpen}
              setRunOpen={setRunOpen}
              running={running}
              llmOk={llmOk}
              webOk={webOk}
              onDiscover={onDiscover}
              onRefresh={onRefresh}
              runStatus={runStatus}
            />
          </div>
        </div>

        <nav className="scrollbar-none flex max-w-full items-center gap-1 overflow-x-auto pb-1 md:flex-1 md:pb-0">
          {TABS.map((t) => (
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
            users={users}
            user={user}
            onUser={onUser}
            runOpen={runOpen}
            setRunOpen={setRunOpen}
            running={running}
            llmOk={llmOk}
            webOk={webOk}
            onDiscover={onDiscover}
            onRefresh={onRefresh}
            runStatus={runStatus}
          />
        </div>
      </div>
    </header>
  );
}

function TopControls({
  users,
  user,
  onUser,
  runOpen,
  setRunOpen,
  running,
  llmOk,
  webOk,
  onDiscover,
  onRefresh,
  runStatus,
}: {
  users: string[];
  user: string;
  onUser: (u: string) => void;
  runOpen: boolean;
  setRunOpen: (updater: boolean | ((open: boolean) => boolean)) => void;
  running: boolean;
  llmOk: boolean | undefined;
  webOk: boolean | undefined;
  onDiscover: () => void;
  onRefresh: (options?: Record<string, any>) => void;
  runStatus: RunStatus | null;
}) {
  return (
    <>
      <div className="flex min-w-0 items-center rounded-lg border border-ink-400/60 bg-ink-800 p-0.5">
        {users.map((u) => (
          <button
            key={u}
            onClick={() => onUser(u)}
            className={`rounded-md px-2 py-1 text-xs font-medium transition-colors sm:px-2.5 ${
              user === u ? "bg-pink-accent/20 text-pink-accent" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {u}
          </button>
        ))}
      </div>

      <div className="relative">
        <button
          className="btn-primary px-2.5 py-1 text-xs sm:px-3 sm:py-1.5 sm:text-sm"
          onClick={() => setRunOpen((o) => !o)}
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
              label="Refresh offers"
              hint="Static/cache first, no web fallback"
              disabled={!llmOk || running}
              onClick={() => {
                setRunOpen(false);
                onRefresh({ limit: null, only_stale: true, include_incomplete: true, use_web_search: false, refresh_valuations: false });
              }}
            />
            <RunItem
              label="Deep refresh"
              hint="Uses capped web fallback for unresolved cards"
              disabled={!llmOk || !webOk || running}
              onClick={() => {
                setRunOpen(false);
                onRefresh({ limit: null, only_stale: true, include_incomplete: true, use_web_search: true, web_fallback_limit: 8, refresh_valuations: false });
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
            <RunItem label="Run award search" hint="Later phase - needs a live award API" disabled />
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

      <StatusPill runStatus={runStatus} />
    </>
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
      className="w-full rounded-md px-2 py-2 text-left hover:bg-ink-600 disabled:opacity-40 disabled:cursor-not-allowed"
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
