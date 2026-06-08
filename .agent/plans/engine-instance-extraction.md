# Engine / Instance Extraction — the shared-code model, settled

**Decision (2026-06-08):** Shared code is shared by **`git pull` from one canonical engine
repo**, never ported. A brain = **engine clone + brain-local instance layer**. Bundled
port-kit assets are a **narrow stopgap only**, retired once a brain is on engine-pull.

**Why now:** the LisaOS diagnose→bootstrap run proved porting doesn't scale — 1 of ~16
shared-code gaps applied (the bundled guard); the other ~15 (scrubs, privacy gate, vector RAG,
monitoring, dreaming, gap-analysis, comms-capture) blocked because you'd have to duplicate the
whole repo as assets. LisaOS's separate, diverged repo is the actual defect. Pull is also more
secure: a brain gets the *entire* security stack atomically on pull, vs. missing one when ported
piecemeal (exactly the privacy gate LisaOS lacks today).

This is also **required for the product**: clients can't clone `brunobouwman/brunOS` (your
private personal repo). The canonical must be a clean engine repo with zero personal content.

---

## 1. The two-layer model

| Layer | Contents | Propagation |
|---|---|---|
| **ENGINE** (shared, identical every brain) | `.claude/scripts/**`, `.claude/hooks/**`, `.claude/chat/**`, `.claude/settings.json` (portable hooks), shared skills (`diagnose-brain`, `bootstrap-brain`, `company-*`, `news-digest`, `weekly-review`, `dev-task`, `code-review`, `create-second-brain-prd`, `skill-creator`), `tests/**`, `eval/**`, `deploy/**` (templates + scripts), `pyproject.toml`, `uv.lock`, `setup.sh`, `.python-version`, `.gitignore`, **instance templates** (`CLAUDE.template.md`, `brain-config.template.json`, `.claude/.env.example`, seed templates) | **`git pull`** from the canonical engine repo |
| **INSTANCE** (brain-local, per-brain) | `CLAUDE.md`, `.claude/.env`, `.claude/data/**`, `.claude/settings.local.json`, `brain-config.json`, the **vault** (`Memory/`), brain-local skills (`vault-structure`/`brunos-vault`, `memory-search`), generated systemd/launchd units | **generated** by the onboarding spec + bootstrap; **never pulled** |

**The line — one question:** *same for every brain → engine; specific to one brain → instance.*

### The tricky boundary calls (resolved)

- **`CLAUDE.md` → INSTANCE.** It declares "I am BrunOS" + BrunOS's deployment. A clone must
  NOT inherit BrunOS's identity. The engine ships `CLAUDE.template.md`; each brain's `CLAUDE.md`
  is generated from its onboarding spec (like the vault skills, now gitignored). **This is new
  brain-local work** (parallels #35).
- **`.claude/settings.json` → ENGINE.** Now that hooks are portable (`$CLAUDE_PROJECT_DIR`),
  settings.json is identical across brains → shared. Host/brain-specific overrides go in
  `settings.local.json` (instance, already gitignored convention).
- **`deploy/**` → ENGINE (templates + scripts).** The *generated* units + per-host env/paths
  are instance (produced by `gen_schedules.py` from `brain-config.json`).
- **Brain-local skills** (`brunos-vault`/`vault-structure`, `memory-search`) → already INSTANCE
  (gitignored #35). ✓

---

## 2. The canonical engine repo

- **What it is:** the ENGINE layer above, with **zero personal content** — generic templates
  where BrunOS-specifics used to be. Developed on Mac (Bruno), pushed by Bruno; every brain
  (incl. BrunOS's own instance) clones + pulls it.
- **Name / host — OPEN (Bruno's call):** e.g. `protostack/brain-engine` (a Protostack-owned
  private repo, read-pull for client brains). NOT `brunobouwman/brunOS`.
- **History:** OPEN — fresh `git init` (simplest, clean slate) vs. filtered history. Lean fresh.
- **Client read access:** clients clone via a read-scoped deploy key / token to the engine repo;
  they never see another brain's instance layer (it's never in the engine repo).

**Net effect:** even **BrunOS stops being "the repo that's both engine and Bruno's instance."**
It becomes engine-clone + BrunOS instance layer — same as every other brain. The engine repo is
the single source of shared truth.

---

## 3. Migration phases (ordered, each independently shippable + testable)

> Safety principle: **test on LisaOS first** (the brain that needs it, lowest blast radius),
> **BrunOS last** (most production-critical). Never break a running daemon. Each phase is its own
> reviewed PR; nothing auto-merges.

**P1 — Freeze the boundary manifest** (this PRD). Output: the engine/instance table above, agreed.

**P2 — Make `CLAUDE.md` brain-local** (precursor, doable in the current repo). gitignore +
`git rm --cached CLAUDE.md`; add `CLAUDE.template.md`; have `create-second-brain-prd` emit a
brain's `CLAUDE.md` from its spec, and `bootstrap-brain` place it. **Same VPS-deletion caveat as
the vault skills**: after merge, re-seed each host's `CLAUDE.md` (Mac keeps its copy).

**P3 — Create the engine repo.** Carve the ENGINE layer from the current repo into the new
canonical (fresh init); strip personal content; add the instance templates; wire `uv sync` +
`setup.sh`; run the full test suite + `diagnose-brain` against a throwaway clone.

**P4 — Migrate LisaOS first** (the live proof). Re-point her code to an engine clone, preserving
her instance layer (vault, env, config, local-skills, CLAUDE.md). Her ~15 missing modules arrive
in **one pull**. Validate with `diagnose-brain` → the shared-code findings clear. This is the
test that the whole model works on a separate-repo brain.

**P5 — Migrate LinOS.** It already pulls `brunobouwman/brunOS`; just re-point the remote to the
engine repo + reconcile its instance layer. Validate with `diagnose-brain` (company role).

**P6 — Migrate BrunOS (Mac + VPS) last.** Mac becomes engine-clone (where engine dev happens) +
BrunOS instance; VPS code-sync re-points to the engine repo. Do this carefully, off-peak, with
the daemons' graceful restart; rollback = re-point the remote. Validate with `diagnose-brain`.

**P7 — Reframe bootstrap + onboarding to the engine model.**
- `bootstrap-brain`: a shared-code finding's remediation becomes **"ensure engine-clone + pull"**
  (a new action `R0 · align-to-engine`), NOT "port asset." Retire the bundled port-kit assets to
  stopgap-only status (keep the guard asset for pre-migration/air-gapped cases; note it's not the
  mechanism).
- Greenfield: clone the engine + lay the instance (from the onboarding spec) + pull = a complete,
  current, secure brain. The "partially-recorded greenfield" actions collapse into "clone engine +
  apply instance."

---

## 4. Safety, sequencing, rollback

- **Order:** LisaOS → LinOS → BrunOS (least → most production-critical).
- **Daemons:** migrate while stopped or via graceful restart; the slackbot's flush+resume makes a
  recycle lossless (CLAUDE.md doc'd).
- **Per-brain rollback:** re-point the git remote back; the instance layer is untouched (it's
  separate). Because instance ≠ engine, a bad engine pull can't corrupt a brain's vault/config.
- **Pre-migration freeze:** land P2 (CLAUDE.md brain-local) + the current open PRs (#37) first so
  the instance boundary is clean before the carve.

---

## 5. Decisions (resolved 2026-06-08)

1. **Engine repo:** `protostack/brain-engine` — private, Protostack-owned. ✅
2. **History:** **fresh `git init`** — the old `brunobouwman/brunOS` history carries personal
   content (vault refs, possibly early secrets); a client-readable repo must start clean. ✅
3. **Release model (versioned stable channel):**
   - **`main`** = development. **`stable`** = promoted, **semver-tagged** releases (`v1.x.y`).
   - **Release gate** ("reaches stability"): full test suite green + `diagnose-brain` green +
     **run clean on BrunOS for a cycle**. **BrunOS is the canary** — Bruno's daily use IS the
     stability test; when a version proves out on BrunOS, promote `main → stable` (tag it).
   - **Channels:** **BrunOS pulls `main`** (dogfoods bleeding edge); **LisaOS / LinOS / every
     client pulls `stable`** (tested releases only). A bad commit can't reach a client — it must
     survive Bruno's own usage first.
   - **Access:** per-client **read-scoped deploy key / token** to the engine repo; their
     code-sync pulls the `stable` branch (or pins a tag). Same `git pull` mechanism, pointed at
     `stable`.
4. **BrunOS-Mac:** **one clone** (engine-dev + BrunOS instance together, instance layer
   gitignored alongside) — keep the current workflow; two clones buy isolation we don't need. ✅

### Effect on the migration phases

- **P3** (create engine repo) also sets up the **`main`/`stable` branch model + the release gate**
  (CI: test suite + a `diagnose-brain` smoke clone). Tag the first cut `v1.0.0` on `stable`.
- **P4–P6** (per-brain migration): each brain's code-sync points at the right **channel** —
  BrunOS→`main`, LisaOS/LinOS→`stable`. Clients (later) get a read-scoped key + `stable`.

---

## 6. What this settles

- Shared code: **pull**, never port. Assets: stopgap only.
- `CLAUDE.md` + vault skills + config + vault + env = **instance** (per-brain, generated, never pulled).
- Everything else = **engine** (one canonical repo, every brain clones + pulls).
- LisaOS's 15 missing modules: fixed by **one pull** after P4, not 15 port-kits.
- The product: clients clone the clean engine + their generated instance — uniform, secure,
  current, with zero exposure to another brain's content.
