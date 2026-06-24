# Project Evolution

This is the living change/audit ledger for the Churn codebase.

Keep entries concise and focused on code behavior, data model changes, verification, and rollback notes. Do not record private household data, real account details, secrets, local absolute paths, browser profiles, local database contents, or user-specific app state.

## 2026-06-24 - GitHub Fresh-Slate Preparation

**Status:** completed. **Scope:** repository hygiene, security, documentation.

**What Changed**

- Prepared the repository for private GitHub storage as a reusable bare-bones application structure.
- Converted shipped two-user defaults and documentation examples to generic `User A` / `User B` language.
- Removed historical project-state notes from this ledger to avoid committing private workflow details or local verification state.
- Added a rule that this log must not contain private data, secrets, absolute local paths, or runtime state.
- Made the default profile roster configurable through `CHURN_USERS`.
- Hardened `.gitignore` for env variants, runtime logs, local browser profiles, databases, build output, virtualenvs, and local agent/tool state.
- Removed local `.env`, SQLite/cache data, runtime logs, browser profiles, and local tool state from the working folder where Windows allowed removal.

**Verification**

- Secret/name/path scan found no real profile names, absolute local workspace paths, or obvious token/key patterns in the publishable tree.
- `git ls-files` scan found no tracked local DB/log/build/data artifacts except `.env.example`, which is intentionally tracked.
- Backend compile/import passed using temporary SQLite/cache paths.
- Frontend typecheck passed.
- Frontend build passed; existing Vite large-bundle warning remains.
- Backend tests passed: 75 tests.

**Rollback Notes**

- Restore the previous documentation history only from a private local backup if needed. Do not publish private historical app-state notes.
