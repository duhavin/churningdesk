# September 2026 enhancement contract

IntentGate: Dashboard, Pipeline and Household should identify the same best
household move, explain a binding wait or condition, and show at most two
conditional successors recomputed after that hypothetical move. Existing
points, Chase-under-5/24, Ink, exceptional-value and referral ordering remains
authoritative. A WAIT can rank first. No optimizer or invented issuer policy.

Snapshot: active `backend/logic`, `backend/ingestion`, routers and React
`frontend`; main with 15 preexisting dirty entries at intake. Mode
personal-autopush; root owns the final Git/sanitize authority review. PRIVATE
HeldCard/UserProfile/targeted offers remain separate from PUBLIC ingestion;
`data/`, logs, `.env`, real household rows and live services are forbidden.

TOM: T2; Astra scopes/reviews, Luna Max owns one story at a time; root owns the
independent program gate. Context tools decision: direct source and caller
traces suffice; CodeGraph/Repomix skipped. Consultation decision: no external
source package; implement the approved source-confirmed contract locally.

1. W1 — implementation and local verification GREEN. Shared the documented strategy comparator and one canonical
   household next-move projection. Recompute successors from a cloned current
   DecisionContext after a hypothetical first application or wait. Carry active
   min-spend commitments; unknown available organic spend makes the opportunity
   conditional and visible. No private persistence write from planning.
   The existing private profile/settings flow may accept optional organic
   monthly spending capacity as that person's allocation before active bonus
   commitments. Unknown stays conditional; explicit null clears the value,
   omitted fields preserve it, and active remaining commitments subtract once.
   Never duplicate a household budget across people or infer capacity from
   account balances. Use the existing additive migration and encryption path.
   Ownership: logic/pipeline, household, DecisionContext and direct API/UI
   consumers plus tests. Prove all three surfaces agree; hypothetical holds,
   5/24/Ink spacing, referral and spend burden alter subsequent eligibility.
2. W2 — implementation and local verification GREEN; root independent final review passed. Preserve valid offer expiration from extraction through
   validation/public persistence and per-field evidence into the decision.
   An unrelated fresh field must not refresh old bonus/spend/window evidence.
   Expired or unknown-current terms cannot produce unconditional apply.
   Use the existing additive migration registry if needed; preserve encryption,
   public/targeted separation and review gates. Prove ingest→decision→all three
   surfaces with fictional current, expired, stale and unknown offers.

Final joined behavior: capacity is checked cumulatively at the candidate and
every active commitment deadline, including commitments due after the candidate
window. Only complete monthly allocations are available at each date. A higher
private offer remains conditional until its own spend/window terms are confirmed;
public terms do not certify a different private offer. Referral actions and
user-specific pipeline labels use the same condition projection. Fresh field
evidence uses a UTC timestamp comparison against the local planning-day boundary.

Local evidence: 23 focused and all 259 backend tests passed (full 3.738s;
isolated wrapper 5.218s), frontend typecheck and isolated production build passed.
Real API/browser checks cover Dashboard, Pipeline, Household, conditional referral
actions, and capacity save/clear on desktop, phone with touch/DPR2, and tablet.
Artifacts are workspace-local under
`toolbench/outputs/enhancement-20260905/wewards-final/` (outside this repo).
The browser uses fictional disposable data and locally substituted font CSS;
external font delivery and deployed runtime are not claimed. Root passed the
independent whole-project gate and isolated the 33-path source checkpoint from
prior ingestion work. That exact publication candidate also passed 252 backend
tests, typecheck, build and 27 browser cases, plus sanitize/index review.
No application, provider request, live-state mutation, restart or deployment
was performed.

Verification: existing `.venv/Scripts/python.exe` and stdlib backend.tests;
frontend typecheck/build; isolated fictional desktop/phone checks for changed
flows. Stop for sensitive data, policy ambiguity, installs, unapproved remote
actions or repeated unexplained failures. Root owns the standing personal-autopush
checkpoint/publication assessment after verification and sanitize; unrelated or
private work must never enter that checkpoint. Workers do not commit, push,
restart services or deploy.
Rollback is limited to these edits and preserves prior public-fetch changes.
Coordinator updates CURRENT_STATE and writes PROJECT_EVOLUTION last.
