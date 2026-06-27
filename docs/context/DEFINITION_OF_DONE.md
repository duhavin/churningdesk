# DEFINITION OF DONE — checklist for every change

Every code change must pass all of these before it's considered done.

## Alignment
- [ ] Advances `VISION.md`; does not drift from it.
- [ ] Satisfies `DECISION_RULES.md` — **value-first, not peak-% first**. peak_score is timing,
      not the value ranker. Large transferable bonuses beat small near-peak ones.

## Data integrity
- [ ] Accurate, provenance-carrying data (`source_url`, `last_verified`, evidence). **No
      fabricated values.** Unknown peak/value → `NEEDS DATA`, never a guess.
- [ ] Public vs targeted offers kept separate; peak stays monotonic (decreases → review).
- [ ] Web search only as a cached last resort (stale + hard-to-find + cooldown); routine
      refresh stays cheap.

## Decisions
- [ ] No redundant recommendations (held cards / family dupes excluded; re-eligible →
      "requeue").
- [ ] Household-aware (both users; referrals both directions where applicable).
- [ ] Every recommendation states its **binding reason**.

## Safety / structure
- [ ] PUBLIC/PRIVATE firewall intact — ingestion touches PUBLIC tables only; PRIVATE figures
      stay encrypted; PRIVATE data never reaches the LLM.
- [ ] Any new DB column registered in `backend/db.py::_ADDED_COLUMNS`.
- [ ] cpp convention respected (dollars = points × cpp / 100).

## Quality gates
- [ ] Backend changed modules `py_compile`; `python -c "import backend.main"` clean.
- [ ] Frontend `tsc --noEmit` clean (if frontend touched).
- [ ] Relevant logic has a quick test (seeded temp DB) proving the behavior.
- [ ] Docs in `docs/context/` updated if the change alters intent, data model, or rules.

## Workspace
- [ ] Changes confined to `<repo-root>`. `<separate-wewards-control-repo>`
      untouched.
