import type { HouseholdNextMove, HouseholdMove } from "../lib/api";
import { Card, StatusBadge, cardName, decisionCondition, decisionDisplayStatus } from "./ui";

export function NextMove({ projection }: { projection?: HouseholdNextMove | null }) {
  const primary = projection?.primary;
  const successors = projection?.successors?.slice(0, 2) ?? [];

  return (
    <Card className="border-cyan-accent/20 bg-cyan-accent/5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-cyan-200/70">Household next move</div>
          <div className="mt-1 text-sm font-semibold text-slate-100">
            {primary ? cardName(primary) : "No ranked move"}
          </div>
          {primary && <div className="mt-0.5 text-xs text-slate-400">Applicant: {primary.user}</div>}
        </div>
        {primary && <StatusBadge status={decisionDisplayStatus(primary)} />}
      </div>
      {primary?.reason && <div className="mt-2 text-xs text-slate-300">{primary.reason}</div>}
      {primary && <OfferEvidence move={primary} />}
      {primary?.condition_reason && (
        <div className="mt-1 text-xs text-amber-200">Conditional prerequisite: {primary.condition_reason}</div>
      )}
      {projection?.wait_until && (
        <div className="mt-1 text-xs text-amber-200">Recheck after {projection.wait_until}.</div>
      )}
      {projection?.successor_reason && (
        <div className="mt-2 text-xs text-slate-400">{projection.successor_reason}</div>
      )}
      {successors.length > 0 && (
        <div className="mt-3 space-y-1.5">
          <div className="text-xs text-slate-500">After this move</div>
          {successors.map((move) => <Successor key={`${move.user}-${move.id}`} move={move} />)}
        </div>
      )}
    </Card>
  );
}

function Successor({ move }: { move: HouseholdMove }) {
  return (
    <div className="rounded-md border border-ink-400/70 bg-ink-900/60 px-2 py-1.5">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-200">
        <span className="font-medium">{move.user}</span>
        <span className="min-w-0 flex-1 truncate">{cardName(move)}</span>
        <StatusBadge status={decisionDisplayStatus(move)} />
      </div>
      {(move.conditional || decisionDisplayStatus(move) === "CONDITIONAL") && (
        <div className="mt-1 text-[11px] text-amber-200">
          Conditional prerequisite: {move.condition_reason || decisionCondition(move) || "Confirm the recorded spend terms first."}
        </div>
      )}
      <OfferEvidence move={move} compact />
      {!move.conditional && move.reason && (
        <div className="mt-1 line-clamp-2 text-[11px] text-slate-400">{move.reason}</div>
      )}
    </div>
  );
}

function OfferEvidence({ move, compact = false }: { move: HouseholdMove; compact?: boolean }) {
  const quality = move.current_offer_quality;
  const status = move.current_offer_status || quality?.status;
  if (!status) return null;
  const fresh = status === "fresh";
  const expiration = move.offer_expiration || quality?.expiration;
  const detail = fresh
    ? expiration
      ? `Public offer evidence current through ${expiration}.`
      : "Public offer evidence current; expiration is unknown."
    : move.current_offer_reason || quality?.reason || "Confirm current public offer terms before applying.";
  return (
    <div className={`mt-1 text-[11px] ${fresh ? "text-slate-400" : "text-amber-200"} ${compact ? "line-clamp-2" : ""}`}>
      {detail}
    </div>
  );
}
