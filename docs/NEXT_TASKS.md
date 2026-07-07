# WEwards Next Tasks

This is the lightweight current-priority file. Keep it short and verify against
code and `docs/PROJECT_EVOLUTION.md` before treating any item as current.

## Current Priority

Keep the core household decision loop reliable and professional:

1. Accurate public card data with provenance.
2. Correct private household state.
3. Value-first scoring.
4. Explainable apply/referral/benefit actions.
5. Clean page contracts and usable UI.

## Near-Term Work Candidates

0. Run a catalog refresh so the new referral research query populates
   `referral_bonus_points` (0/43 before 2026-07-06 — the query type didn't
   exist) and re-fills peak targets under the hardened text gates. Web-search
   cost applies; run when Davin wants current data.

1. Improve data-quality visibility in Card Universe and user-facing `NEEDS DATA` states.
2. Tighten Dashboard/Pipeline/Card Plan surfaces so the next action and binding
   reason are obvious.
3. Expand focused tests around scoring, pipeline ordering, public/private
   firewall, and review queue behavior.
4. Improve research resolver/source adapters from real failure cases, not broad
   speculative scraping.
5. Add/refine UI states only where the app cannot clearly answer the next decision.
6. Use `docs/AUDIT_PROMPT.md` for future codebase audits so findings stay
   consistent and comparable across sessions.

## Deferred / Do Not Build Yet Without Approval

- Automatic real-world applications.
- Private-data LLM analysis.
- Broad redesign of the full app.
- Live award-search dependency as a core workflow.
- Advanced redemption optimization beyond the current foundation.
- New migration framework.
- New scoring engine that bypasses `DECISION_RULES.md`.

## Agent Instruction

For a request like "make this professional grade," identify the exact
page/workflow and data contract, then implement one scoped, testable slice. Do
not bundle unrelated UI redesign, ingestion changes, and scoring changes.
