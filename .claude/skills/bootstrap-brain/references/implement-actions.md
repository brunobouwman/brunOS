# Implement-Action Catalog

> Each action is a **known-exact, idempotent** unit keyed to a `diagnose-brain` check. Format:
> **fixes** (the check) · **scope** · **prereqs** · **idempotency** (skip-if) · **steps** ·
> **validate**. The reconciler (SKILL.md Phase 3) applies only the actions whose check is
> failing, in prerequisite order, dry-run → approve → apply.
>
> **Reconcile actions** (below) are complete and proven — they're the fixes verified on BrunOS
> + surfaced by the LisaOS diagnosis. **Greenfield actions** are partially recorded (see the
> end); add proven steps from `onboarding_installer.md` + `deploy/bin/`, never fabricate.

Legend — **scope**: `this-brain` (apply here) · `shared-code/pull` (clone of the canonical
repo: arrives via `git pull`, don't re-apply) · `shared-code/port` (separate-repo brain:
copy the artifact from `assets/`).

---

## R1 · Portable hook commands  ·  fixes: J8(4a), I1 (hooks actually fire)

- **Scope:** this-brain (per host).
- **Prereqs:** none. **RUN THIS FIRST** — it's the prerequisite for R2 (a guard wired to a
  dead path no-ops).
- **Why:** a brain whose `settings.json` (or `settings.local.json`) hooks use **absolute
  host paths** (e.g. `/Users/lisa/...`) **silently no-op on a different host** (the path
  doesn't exist on the Linux VPS) — so NONE of its PreToolUse hooks enforce there.
- **Idempotency / skip-if:** every PreToolUse hook command already uses the portable form
  `cd "$CLAUDE_PROJECT_DIR" && uv run python .claude/hooks/<hook>.py`.
- **Steps:** rewrite each hook `command` in `.claude/settings.json` to the portable form
  above (relative `.claude/hooks/...`, `$CLAUDE_PROJECT_DIR`, `uv run python`). Do the same in
  `settings.local.json` if it overrides hooks. Preserve hook order + matchers.
- **Validate:** `diagnose-brain` I1 passes; the hooks fire on the VPS (test a `.env` read → blocked).

## R2 · Path-boundary guard  ·  fixes: J8(4b)

- **Scope:** `shared-code/port` for a separate-repo brain (install `assets/path-boundary.py`);
  `shared-code/pull` for a clone of the canonical repo (arrives with the merged hook).
- **Prereqs:** **R1** (portable hook paths) — else the guard's `settings.json` entry no-ops.
- **Why:** daemon (chat/heartbeat) has no human at execution → the hooks ARE the enforcement.
  Without this, out-of-tree writes + single-file deletes are only soft-blocked by SOUL.
- **Idempotency / skip-if:** `.claude/hooks/path-boundary.py` exists AND is registered in
  `settings.json` PreToolUse (`Bash|Edit|Write|MultiEdit|NotebookEdit`).
- **Steps (separate-repo):** copy `assets/path-boundary.py` → `.claude/hooks/path-boundary.py`;
  add the PreToolUse entry (matcher `Bash|Edit|Write|MultiEdit|NotebookEdit`, command the
  portable form from R1); copy `assets/test_path_boundary.py` → `tests/`. The hook is
  brain-agnostic — it resolves the vault via the brain's own `shared.vault_path()` and is
  repo-relative; confirm `GUARDED_CONTEXTS = {"chat","heartbeat"}` matches this brain's
  daemon `CLAUDE_INVOKED_BY` values.
- **Validate:** `uv run python tests/test_path_boundary.py` (all pass); `diagnose-brain` J8(4b) passes.

## R3 · Brain-local vault skills  ·  fixes: J8(3)

- **Scope:** this-brain (per host) — generated, **not** copied (each describes ITS vault).
- **Prereqs:** the vault exists + `_brain-filing-rules.md` / folder tree is known.
- **Why:** `vault-structure` (folder layout/frontmatter/routing) + `memory-search` (this brain's
  `--path-prefix` cheat-sheet) are vault-unique; they're gitignored + brain-local, so code-sync
  never delivers them. Each host needs its own copy under `.claude/skills/` so
  `setting_sources=["project"]` loads them.
- **Idempotency / skip-if:** `.claude/skills/vault-structure/` (or legacy `brunos-vault/`) and
  `.claude/skills/memory-search/` exist with valid SKILL.md.
- **Steps:** GENERATE `vault-structure/SKILL.md` from THIS brain's actual folders + filing rules
  + frontmatter spec (not another brain's); GENERATE `memory-search/SKILL.md` with this brain's
  folder→`--path-prefix` map. Place under `.claude/skills/` (no extra wiring — setting_sources
  auto-discovers). Ensure both are gitignored locally.
- **Validate:** `diagnose-brain` J8(3): expected skills present for the role.

## R4 · Shared-code security/robustness fixes  ·  fixes: I (block-secrets), D (drafts)

- **Scope:** `shared-code/pull` for a clone; `shared-code/port` for a separate-repo brain.
- **Why:** fixes that exist in the canonical repo but a separate/lagging brain lacks — e.g.
  `block-secrets` must catch `google_client_secrets.json`/`google-secrets.json`;
  `drafts.expire_old_drafts` must `rglob` subdirectories.
- **Idempotency / skip-if:** `block-secrets.py` patterns match the google client-secret names;
  `drafts.py` uses `rglob`. (Diagnose surfaces these as `shared-code`-tagged findings.)
- **Steps (separate-repo):** port the specific diffs from the canonical reference — broaden the
  google-secret patterns; switch `glob("*.md")` → `rglob` with relative-subpath preservation.
- **Validate:** the brain's `tests/test_privacy_gate.py` (or block-secrets test) pass; a
  google-client-secret read is blocked.

## R5 · Integration env keys  ·  fixes: E (integration enabled but no-op)

- **Scope:** this-brain.
- **Why:** an integration reads "enabled" (token set) but its reader requires a second var the
  brain mis-named (e.g. `CLICKUP_WORKSPACES` vs a typo) → silent no-op.
- **Idempotency / skip-if:** `query.py <int> <read-subcmd>` exits 0 with real data.
- **Steps:** correct the env var name(s) in `.claude/.env` to match what the reader expects
  (`registry.py` / the integration module). **Never record secret values** — only names.
- **Validate:** `diagnose-brain` E2; `query.py <int>` returns data.

## R6 · MEMORY cap-guard  ·  fixes: B6 / C5

- **Scope:** this-brain (often a code-staleness symptom → see R4/pull).
- **Why:** MEMORY.md over the 8KB cap because reflect appends but the brain's code lacks the
  Phase-B eviction, or `reflect-curate` isn't scheduled.
- **Idempotency / skip-if:** MEMORY.md ≤ 8192B AND `reflect-curate` runs on cadence.
- **Steps:** ensure the code has `_evict_to_archive_if_over_cap` (else port/pull it); ensure the
  `reflect-curate` unit is enabled (`gen_schedules.py` + enable timer); run one curation pass.
- **Validate:** MEMORY.md ≤ cap; over-cap items in `_archive/MEMORY-archive.md`; no
  `curate_memory_over_cap`.

---

## Greenfield actions (partially recorded — ADD from recorded runs, don't fabricate)

For a bare-host bring-up, the ordered surface (doc 04 "Bootstrap surface" + the **LinOS C.5
run logged in `onboarding_installer.md`**, which is the recorded *company-greenfield* reference;
the *producer-greenfield* gets recorded on its first dogfood):

1. Unix user + namespace + log dir + ACLs (`deploy/bin/seed-bruno-on-host.sh` pattern).
2. Clone the code repo + `setup.sh` (`uv sync`).
3. Vault git repo + `deploy/bin/init-vault-sync.sh` (commit identity + concat-both driver).
4. `.claude/.env` seeded (tokens, vault path, healthcheck URLs) — **names not values**.
5. `brain-config.json` (role, cadences, federation, channels).
6. `gen_schedules.py` → install timer units (BRT-pinned).
7. Identity seed — producer: SOUL/USER/MEMORY/HEARTBEAT/HABITS; company: SOUL/COMPANY/
   STANDARDS/DECISIONS/ROUTINES/ACCESS_POLICY/_excluded-people/_brain-filing-rules.
8. R3 (generate the brain-local vault skills) + R1/R2 (portable hooks + guard).
9. `google_token.json` for Gmail/Calendar (OAuth bootstrap).
10. `provision_healthchecks.py` (per brain×host).
11. Enable units (single-instance discipline).

**Rule:** each greenfield step is added to this catalog only once it has a recorded, validated
run (idempotency check + validate line). Until then the reconciler flags it as "needs the
manual greenfield run" rather than executing an unproven provisioning step.
