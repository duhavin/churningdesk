import React from "react";

// --- Status color coding (§10) ---------------------------------------------
const STATUS_STYLES: Record<string, string> = {
  "APPLY NOW": "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  WATCH: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  WAIT: "bg-slate-500/15 text-slate-300 border-slate-500/40",
  "NEEDS DATA": "bg-amber-500/10 text-amber-200 border-amber-500/50 border-dashed",
  "LOW PRIORITY": "bg-slate-700/40 text-slate-400 border-slate-600/40",
  SKIP: "bg-rose-600/15 text-rose-300 border-rose-600/40",
  FUTURE: "bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/40",
};

export function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_STYLES[status] ?? "bg-ink-500 text-slate-300 border-ink-400";
  return (
    <span
      className={`inline-flex items-center justify-center whitespace-nowrap rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${cls}`}
    >
      {status}
    </span>
  );
}

export function Card({
  children,
  className = "",
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  children: React.ReactNode;
  className?: string;
}) {
  return <div className={`card p-3 sm:p-4 ${className}`} {...props}>{children}</div>;
}

export function SectionTitle({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h2 className="text-lg font-semibold text-slate-100">{title}</h2>
        {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-slate-400 text-sm">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-cyan-accent/40 border-t-cyan-accent" />
      {label ?? "Loading…"}
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="card p-8 text-center">
      <p className="text-slate-300 font-medium">{title}</p>
      {hint && <p className="text-sm text-slate-500 mt-1">{hint}</p>}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

export function ScoreBar({ score }: { score: number }) {
  const color =
    score >= 90 ? "bg-emerald-400" : score >= 75 ? "bg-amber-400" : score >= 50 ? "bg-slate-400" : "bg-slate-600";
  return (
    <div className="flex items-center gap-2 min-w-[110px]">
      <div className="h-1.5 flex-1 rounded-full bg-ink-500 overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${Math.min(100, Math.max(0, score))}%` }} />
      </div>
      <span className="font-mono text-xs text-slate-300 w-7 text-right">{score}</span>
    </div>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  wide?: boolean;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-3 sm:p-4" onClick={onClose}>
      <div
        className={`card my-6 w-full ${wide ? "max-w-3xl" : "max-w-lg"} p-4 sm:my-12 sm:p-5`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-slate-100">{title}</h3>
          <button className="text-slate-500 hover:text-slate-200" onClick={onClose}>
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
    </label>
  );
}

export function Banner({ kind, children }: { kind: "warn" | "info" | "error"; children: React.ReactNode }) {
  const styles = {
    warn: "border-amber-500/40 bg-amber-500/10 text-amber-200",
    info: "border-cyan-accent/30 bg-cyan-accent/5 text-cyan-100",
    error: "border-rose-500/40 bg-rose-500/10 text-rose-200",
  }[kind];
  return <div className={`rounded-lg border px-3 py-2 text-sm ${styles}`}>{children}</div>;
}

export function fmtMoney(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString();
}
