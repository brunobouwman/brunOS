# Feature: BrunOS↔LinOS Federation Write-Side — VPS reflection ingests per-project inboxes (content-split + strip-in-place)

The following plan should be complete, but it's important to validate documentation and codebase patterns and task sanity before you start implementing. Pay special attention to naming of existing utils/types/models — import from the right files (`shared.py`, `sanitize.py`) and mirror `memory_reflect.py` exactly.

> **Supersedes** the episodic half of `.agent/plans/phase-b-consolidation-dreaming.md`. Decisions in conversation 2026-05-24 **cut the standalone `memory_consolidate.py` episodic pass** ("compact a done feature instantly" was judged low-value) and **folded its only useful behaviour — draining per-project inboxes — into daily reflection**. The procedural **dreaming → playbook** pass (`memory_dream.py`) remains a *separate, parallel* future plan and is **out of scope here**. The `_shared/linos/{staging,cleaned}` three-zone staging design (daily logs 2026-05-21) is **also dropped** — see "Solution Statement".

## Feature Description

Bruno works most days inside *project* repos (Vertik `lab-agent`/`lab-agent-chat-ui` → slug `vertik`; Protostack client repos → e.g. `colinas`), and Codex/Claude Code session hooks already distil each session into a per-project inbox at `BrunOS/Memory/_inbox/sessions/<slug>/` (gitignored, host-local). **Today nothing consumes those inboxes** — `memory_reflect.py` only reads *yesterday's daily log* + `MEMORY.md`, so all the knowledge captured during project work never reaches Bruno's durable personal brain, and there is no per-project continuity doc beyond a hand-maintained `projects/<slug>.md`.

This feature extends **daily reflection** (running on the always-on VPS at ~06:00 BRT) to also ingest the per-project inboxes and, per capture, produce **three outputs**:

1. **Personal consolidation** — durable personal knowledge → `MEMORY.md` (existing path) and/or a per-project continuity doc.
2. **Per-project continuity** — a distilled `## Auto-consolidated continuity` section in `projects/<slug>.md`, so the `session-start-project.py` SessionStart hook (which loads `--context-file=projects/<slug>.md`) injects clean accumulated context next time Bruno opens that repo.
3. **Strip-in-place + `share_status: cleared`** — the capture is rewritten with personal-life asides removed, stamped `share_status: cleared` in frontmatter, so a downstream *company brain* (LinOS now; VertikOS later) reading the same gitignored inbox sees only work-scoped content. This is the privacy boundary, expressed as a flag instead of a separate staging folder.

Reflection **does not delete or archive** the captures (a separate VPS retirement job does that once both brains have consumed them) and **does not stage** anything to a `_shared/` folder (the company brain reads the per-company inbox directly — see Solution Statement for why raw-read-per-company-inbox is safe).

## User Story

As Bruno (Vertik contractor + Protostack co-founder whose external-repo sessions already land in per-project BrunOS inboxes)
I want my daily reflection to mine those inboxes — pulling work-derived knowledge into my personal brain, maintaining a per-project continuity doc, and stripping personal asides out of each capture in place
So that project-session knowledge stops dying in the inbox, my next session in a repo loads clean continuity, and the joint LinOS brain (and later a VertikOS company brain) can safely read the work-only captures — without me hand-copying context between brains and without any brain writing into another.

## Problem Statement

1. **Per-project knowledge is trapped.** Reflection never reads `_inbox/sessions/<slug>/`; a day spent in `colinas/` or `vertik` produces captures that never reach `MEMORY.md` or any durable personal store.
2. **No auto-maintained per-project continuity.** `projects/<slug>.md` is hand-written; SessionStart leans on raw recent captures for continuity instead of a distilled doc.
3. **No privacy boundary on captures.** A capture can contain personal asides mixed with work; nothing separates them, so a company brain can't safely read a raw capture.
4. **Manual cross-brain context sharing.** Bruno + Lisa currently hand-ask their brains to write to LinOS. There's no automatic flow from individual-brain capture → joint-brain-readable content.

Risk of doing it wrong: personal content leaking to LinOS/VertikOS; `MEMORY.md` blown past its 5 KB cap by project noise; re-processing the same captures every night (cost) or losing them (correctness); a reflection that mutates captures in a way the VPS rsync later clobbers (see NOTES → transport).

## Solution Statement

Extend `memory_reflect.py` with an **inbox stage** that runs in the same daily Sonnet-backed process, mirroring the file's existing shape (single `_reason` SDK call, tolerant JSON parse, deterministic apply, idempotent state, `--dry-run`). One LLM call **per project inbox with new captures** (bounded by *projects touched yesterday*, not total projects). State is a **per-project watermark**.

**Why raw-read-per-company-inbox (no `_shared/` staging):** the old "LinOS never reads raw inboxes" rule was written for a *single big individual inbox*. With **per-company inboxes** (LinOS reads only `linos-protostack`-tagged inboxes like `colinas/`; VertikOS would read only `vertik`; neither touches personal-only inboxes), plus reflection's **strip-in-place** removing personal asides before the company brain reads, the staging copy becomes redundant. The capture *is* the shared artifact; `share_status: cleared` is the gate. This is consistent with the documented "BrunOS↔LinOS and VertikOS share primitives but run different policies" stance — VertikOS can later tighten its destination to a staged/extract model without re-architecting.

**In scope for this plan (BrunOS producer side — buildable + testable now, no LinOS dependency):**

```
.claude/scripts/memory_reflect.py     # EXTEND: add the inbox stage (3 outputs) + per-project continuity + strip-in-place
.claude/data/state/inbox_reflection.json   # NEW: per-project last-processed watermark
BrunOS/Memory/projects/<slug>.md      # auto-maintained "## Auto-consolidated continuity" section (create-if-absent)
CLAUDE.md                             # document the new reflection scope + the federation write-side model
```

**Out of scope here (deferred — require LinOS to be an agent node, i.e. Phase C.5; described as contract in IMPLEMENTATION PLAN Phases 5–6 + NOTES so the producer emits the right shape):** rsync transport units, the VPS retirement/deletion job, the LinOS consumer reflection + ack manifest, Phase D `query()`, cross-VPS production. Also out of scope: `memory_dream.py` (parallel future plan).

## Feature Metadata

**Feature Type**: Enhancement (extends existing `memory_reflect.py`)
**Estimated Complexity**: High (LLM content-split, in-place mutation of captures, per-project continuity with cap/compaction, idempotency via watermark, confidentiality invariants)
**Primary Systems Affected**: `memory_reflect.py`, vault `projects/<slug>.md`, vault `_inbox/sessions/<slug>/` (in-place rewrite), new state file
**Dependencies**: `claude_agent_sdk` (vendored), `shared.py`, `sanitize.py`. No new pip deps.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: READ THESE BEFORE IMPLEMENTING

- `.claude/scripts/memory_reflect.py` (FULL — the canonical mirror). Key anchors:
  - line **19** — recursion guard `os.environ.setdefault("CLAUDE_INVOKED_BY", "reflection")` BEFORE any SDK import. The inbox stage runs in the same process; no new guard needed.
  - lines **123-138** — `_reason()` SDK call shape (`allowed_tools=[]`, `setting_sources=None`, `max_turns=1`, `model=SONNET_MODEL`). Reuse verbatim for the inbox call.
  - lines **156-184** — `_parse_promotions()` tolerant JSON extraction (fence/`[`-`]` fallback). Mirror for the inbox-result parser.
  - lines **187-224** — `_split_memory()` + `_append_promotions()` section-insert into `MEMORY.md` under `SECTION_HEADERS`. Reuse `_append_promotions` for the personal items.
  - lines **237-278** — `_compact_if_over_cap()` (cap guard + abort-on-overshrink). **Generalize** this to accept `(text, cap_bytes)` so it can also cap `projects/<slug>.md`.
  - lines **281-287** — idempotent state via `save_state`/`load_state` on `last_reflection.json`. Mirror with a per-project watermark dict.
  - lines **309-377** — `_run()` flow + the **`--dry-run`** discipline (prints would-be output, no writes, no state update). The inbox stage hangs off this.
- `.claude/scripts/shared.py`:
  - `write_inbox_capture()` (**312-358**) — the EXACT capture format the stage reads: frontmatter `type: inbox / created / updated / project / default_export / session_id / source / tags(block) / status`; body = `## Memory flush (HH:MM)` + bullets. `_VALID_EXPORT_TARGETS = {"personal","linos-protostack","discard"}` (**235**).
  - `atomic_write()` (**184**, stamps `updated:` for `.md`), `file_lock()` (**142**), `_stamp_updated`/`_FM_RE` (**163-181**), `append_to_daily_log()` (**218**), `load_state`/`save_state` (**361-369**), `now_brt`/`_ts_brt` (**79-85**), `vault_path()` (**127**), `_slug()` (**238**), `STATE_DIR`/`REPO_ROOT` (**20-21**), `trim_dedup_entries()` (**516**).
  - `_SLUG_ALIASES` / `canonicalize_slug` (**254-271**) — Vertik unified to `vertik`, colinas→`colinas` (done 2026-05-24).
- `.claude/scripts/sanitize.py`: `wrap_external(content, source, **attrs)` (**80**), `clean_external` (**74**), `TRUST_BOUNDARY_INSTRUCTION` (**11**). Wrap each capture body before it enters the prompt (mirror `aggregate_week.py:335-337`).
- `.claude/hooks/session-start-project.py` (**79-134** `build_context`) — confirms SessionStart loads `--context-file` (relative → resolved under `Memory/`) then recent captures from `_inbox/sessions/<project>/`. **No change needed** — it already loads `projects/<slug>.md`; the continuity section just enriches it.
- `BrunOS/Memory/_inbox/sessions/colinas/2026-05-23-204717-38afd0ea.md` — a REAL capture: trust the on-disk frontmatter (`default_export: linos-protostack`, `status: active`, no `share_status` yet).
- `BrunOS/Memory/projects/vertik.md`, `BrunOS/Memory/projects/colinas.md` — existing per-project docs the continuity section appends into (hand-written header must stay intact).

### New Files to Create

- `.claude/data/state/inbox_reflection.json` — `{ "<slug>": "<last_processed_created_iso>" }` per-project watermark (created on first run via `save_state`).
- (No new scripts — the stage lives inside `memory_reflect.py`. No `_shared/` folders.)

### Relevant Documentation — READ BEFORE IMPLEMENTING

- Claude Agent SDK — `ClaudeAgentOptions` / `query` usage as already used in `memory_reflect.py:124-134` (no external doc needed; mirror in-repo).
- `CLAUDE.md` → "Heartbeat + Reflection (Phase 6)", "`setting_sources` policy", "Recursion guard", "Proactivity / NEVER" boundaries, "YAML frontmatter (every vault file)".
- Federation design (canonical): `BrunOS`-side memory `project_brain_federation.md` + `project_external_repo_session_hooks.md`; joint doc `LinOS/new_second_brain/brain_federation.md`. **These describe the OLD staging design and need updating in Task: docs** (see Phase 4).

### Patterns to Follow

**SDK call** — reuse `_reason()` as-is (`memory_reflect.py:123-138`): `allowed_tools=[]`, `setting_sources=None`, `max_turns=1`, `model=SONNET_MODEL`.

**Tolerant JSON parse** — mirror `_parse_promotions` (`156-184`); the inbox result is a single JSON object (not array) so adapt the fence/brace extraction to `{...}`.

**Vault writes** — always `with file_lock(path): atomic_write(path, text)`. `atomic_write` auto-stamps `updated:` for `.md`. New files carry full frontmatter (`type/created/updated/tags`(block list)/`status`) per CLAUDE.md + the `[[feedback-uniform-frontmatter]]` / `[[feedback-obsidian-yaml-format]]` memory rules.

**Idempotency** — per-project watermark in `inbox_reflection.json`; only process captures with `created > watermark` AND `share_status != cleared` (belt-and-suspenders). Re-run = no-op.

**External-content wrap** — `wrap_external(body, "inbox-capture", project=slug)` before prompt (mirror `aggregate_week.py:335-337`).

**`--dry-run`** — prints the parsed per-project result (personal items + continuity bullets + which captures *would* be cleared); writes nothing; does not advance the watermark. Mirror `memory_reflect.py:354-358`.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation — capture parsing + watermark + per-project memory helpers (no SDK)

Deterministic substrate, fully testable without token spend.

**Tasks:**
- `_parse_capture(path) -> (dict, str) | None` — split frontmatter (reuse `_FM_RE` from `shared.py` or a local copy) into a dict + body. Tolerate missing/malformed frontmatter (skip, log, never crash).
- `_iter_inbox_projects() -> list[str]` — list slugs under `Memory/_inbox/sessions/*` (dirs only, skip `_`-prefixed).
- `_unprocessed_captures(slug, watermark_iso) -> list[Path]` — captures whose frontmatter `created > watermark` AND `share_status != "cleared"`, sorted ascending by `created`.
- Watermark read/advance over `inbox_reflection.json` via `load_state`/`save_state`.
- `_append_continuity(slug, bullets)` — open/create `projects/<slug>.md` (full frontmatter if absent: `type: project`), insert bullets under `## Auto-consolidated continuity` (create section if absent), then cap via the generalized `_compact_if_over_cap(text, cap_bytes)`.
- `_strip_and_mark_capture(path, fm, cleaned_body)` — rewrite the capture: keep frontmatter, set/insert `share_status: cleared`, replace body with `cleaned_body`; `file_lock` + `atomic_write`. **Never delete/move** (retirement is a separate deferred job).

### Phase 2: Core — the inbox reflection stage (one Sonnet call per project)

- `INBOX_SYSTEM_PROMPT` instructing the model, per project, to return **one JSON object**:
  ```json
  {
    "personal": [{"type":"decision|lesson|fact|status","text":"...","promote":true}],
    "continuity": ["distilled project-state/reference bullet", "..."],
    "cleaned_captures": [{"capture":"<filename>","body":"<work-only body, personal-life asides removed, work content preserved>"}]
  }
  ```
  Rules in the prompt: preserve all work/technical content verbatim in `cleaned_captures.body`; remove only personal-life asides (mood, family, unrelated personal notes); NEVER invent; if a capture has no personal content, return its body unchanged; cap `personal` at 8 items/project; output raw JSON only.
- `_parse_inbox_result(raw) -> dict | None` (tolerant, mirrors `_parse_promotions`).
- `_run_inbox_stage(dry_run)`: for each project with unprocessed captures → build prompt (`wrap_external` each capture body, keyed by filename) + current `projects/<slug>.md` → `_reason(..., system_prompt=INBOX_SYSTEM_PROMPT)` → parse → apply: `_append_promotions(MEMORY)` for `personal`, `_append_continuity(slug)` for `continuity`, `_strip_and_mark_capture` for each `cleaned_captures` entry (match by filename) → advance watermark to max(`created`) processed.

### Phase 3: Integration — wire into `_run` + CLI

- Call `_run_inbox_stage(dry_run)` from `_run()` **after** the existing daily-log→`MEMORY.md` stage (so MEMORY compaction runs once at the end is acceptable; or compact after both). Keep the daily-log stage unchanged.
- CLI flags: keep `--dry-run`; add `--inbox-only` (skip daily-log stage), `--skip-inbox` (legacy behaviour), `--project <slug>` (limit to one inbox). Default = both stages.
- Confidentiality invariant: the stage treats `default_export` purely as metadata it preserves; it does **not** route to LinOS (the company brain reads the inbox itself). The only cross-brain-relevant output is `share_status: cleared`. There is **no path that writes outside the BrunOS vault.**

### Phase 4: Validation + docs

- `--dry-run` end-to-end against the real (copied) `colinas/` + `vertik/` inboxes; confirm zero vault writes.
- Real run on a **copy**; confirm `projects/<slug>.md` continuity grows, captures stamped `cleared` + personal stripped, `MEMORY.md` stays ≤5 KB, watermark advances, re-run = no-op.
- Update `CLAUDE.md` (new reflection scope + write-side model), and the federation docs (`project_brain_federation.md`, `project_external_repo_session_hooks.md`, `LinOS/new_second_brain/brain_federation.md`) to **supersede** the staging/consolidate design with: per-company-inbox raw-read, reflection strip-in-place + `share_status: cleared`, VPS-side retirement + ack + 15-day fallback, gitignore+rsync orthogonality, reflection-runs-on-VPS.

### Phase 5 (DEFERRED — needs C.5): Transport + retirement (contract only)

Documented so the producer emits the right shape; **not built here** (LinOS isn't an agent yet):
- **rsync** Mac→VPS, `-a --update` (deliver-only, never `--delete`) so the VPS's cleaned/`cleared` captures are not clobbered by the Mac's raw originals. Resurrection guard: Mac self-prune (age-based local cleanup) and/or `--exclude-from=<retired-list>`.
- **VPS retirement job** (deterministic timer or reflection extension): delete a capture once `BrunOS-processed (watermark) AND LinOS-acked (LinOS manifest) ELSE 15-day fallback`. Decision lives on the VPS where both watermark + ack co-reside.

### Phase 6 (DEFERRED — needs C.5): LinOS consumer + ack (contract only)

- LinOS (its own private gh repo on the VPS, after C.5) reads only `linos-protostack`-tagged, `share_status: cleared` captures from Bruno's + Lisa's inboxes, integrates into its own taxonomy, **writes only to itself**, and publishes an ack (`{capture, content-hash}`) to **its own** manifest. No brain writes/deletes inside another.

---

## STEP-BY-STEP TASKS

Execute in order. Each is atomic and independently testable. **Phases 5–6 are contract-only — do NOT implement them in this pass.**

### UPDATE `.claude/scripts/memory_reflect.py` — add constants + capture parsing (Phase 1a)
- **IMPLEMENT**: `INBOX_WATERMARK_PATH = STATE_DIR / "inbox_reflection.json"`; `PROJECT_DOC_CAP_BYTES = 8192`; `CONTINUITY_HEADER = "## Auto-consolidated continuity"`; `_parse_capture(path)`; `_iter_inbox_projects()`; `_unprocessed_captures(slug, watermark)`.
- **PATTERN**: frontmatter regex `_FM_RE` (`shared.py:163`); state via `load_state/save_state` (`shared.py:361-369`); slug list from `vault_path()/"Memory"/"_inbox"/"sessions"`.
- **IMPORTS**: add to the existing `from shared import (...)` block: `_slug` (and reuse already-imported `STATE_DIR, vault_path, load_state, save_state, file_lock, atomic_write, now_brt, _ts_brt`).
- **GOTCHA**: `created` is RFC3339 with `-03:00`; compare as strings only if same format, else parse — safest to parse with `datetime.fromisoformat`. Skip captures with malformed frontmatter (log, continue).
- **VALIDATE**: `uv run python -c "import sys;sys.path.insert(0,'.claude/scripts');import memory_reflect as m;print(m._parse_capture(__import__('pathlib').Path('BrunOS/Memory/_inbox/sessions/colinas/2026-05-23-204717-38afd0ea.md'))[0]['project'])"` → `colinas`.

### UPDATE `.claude/scripts/memory_reflect.py` — generalize compaction + per-project memory writer (Phase 1b)
- **IMPLEMENT**: refactor `_compact_if_over_cap(text)` → `_compact_if_over_cap(text, cap_bytes=MEMORY_HARD_CAP_BYTES)`; add `_append_continuity(slug, bullets)` that creates `projects/<slug>.md` (frontmatter `type: project`, tags block) if absent, inserts bullets under `CONTINUITY_HEADER` (mirror `_append_promotions` section-insert at `195-224`), then caps to `PROJECT_DOC_CAP_BYTES`.
- **PATTERN**: `_append_promotions` (`195-224`); `_new_daily` frontmatter style (`shared.py:202-215`) for the create-if-absent block.
- **GOTCHA**: preserve the hand-written header above `CONTINUITY_HEADER`; only the continuity section is machine-managed. `atomic_write` auto-stamps `updated:`.
- **VALIDATE**: unit-call `_append_continuity("scratch-test", ["x"])` against a temp vault copy → file created with valid frontmatter + section; then `uv run python .claude/scripts/memory_index.py --paths BrunOS/Memory/projects/scratch-test.md --dry-run` exits 0. (Delete scratch after.)

### UPDATE `.claude/scripts/memory_reflect.py` — strip-in-place writer (Phase 1c)
- **IMPLEMENT**: `_strip_and_mark_capture(path, fm, cleaned_body)` — rebuild the file = frontmatter (with `share_status: cleared` set/inserted, `updated:` restamped by `atomic_write`) + `cleaned_body`; `file_lock` + `atomic_write`. **Never `unlink`/`move`.**
- **GOTCHA**: inserting `share_status:` into existing frontmatter — add after `status:` line; idempotent if already present. Body must remain valid markdown.
- **VALIDATE**: on a COPY, run `_strip_and_mark_capture` → assert file still exists, frontmatter has `share_status: cleared`, body changed; `git status BrunOS/Memory` clean after reverting the copy.

### UPDATE `.claude/scripts/memory_reflect.py` — inbox prompt, parser, stage (Phase 2)
- **IMPLEMENT**: `INBOX_SYSTEM_PROMPT` (schema above), `_parse_inbox_result(raw)` (tolerant `{...}` parse mirroring `_parse_promotions`), `_run_inbox_stage(dry_run)`.
- **PATTERN**: `_reason` (`123-138`); `_parse_promotions` (`156-184`); `wrap_external` (`sanitize.py:80`) per capture body.
- **IMPORTS**: `from sanitize import wrap_external`.
- **GOTCHA**: one call PER project (bounded context, parallelizable later). Match `cleaned_captures[].capture` to the real filename before rewriting (ignore unmatched names — model hallucination guard). On parse failure: `_dump_debug` + skip that project (don't crash the whole run, don't advance its watermark).
- **VALIDATE**: `uv run python .claude/scripts/memory_reflect.py --dry-run --inbox-only` prints per-project parsed JSON; `git status BrunOS/Memory` shows no changes.

### UPDATE `.claude/scripts/memory_reflect.py` — wire into `_run` + CLI flags (Phase 3)
- **IMPLEMENT**: call `_run_inbox_stage(dry_run)` from `_run` after the daily-log stage; add `--inbox-only`, `--skip-inbox`, `--project <slug>` to `main`.
- **GOTCHA**: keep the existing daily-log→`MEMORY.md` behaviour and its `last_reflection.json` dedup intact; the inbox stage uses its OWN `inbox_reflection.json`.
- **VALIDATE**: `uv run python .claude/scripts/memory_reflect.py --dry-run` runs both stages, writes nothing.

### UPDATE `CLAUDE.md` + federation docs (Phase 4)
- **IMPLEMENT**: new "Reflection (Phase B write-side)" notes in `CLAUDE.md` (per-company-inbox raw-read, strip-in-place + `share_status: cleared`, reflection on VPS, deferred retirement/consumer/transport); update the three federation docs to supersede the staging/consolidate design.
- **VALIDATE**: `grep -c "share_status" CLAUDE.md` ≥ 1; `grep -ci "supersed" LinOS/new_second_brain/brain_federation.md` ≥ 1.

---

## TESTING STRATEGY

No test suite exists (no `tests/`, no pytest config). Match the project idiom: **`--dry-run` + targeted manual runs against a copied inbox** (how Phases 5–6 were validated).

### "Unit" checks (no SDK)
- `_parse_capture` round-trips a real capture's frontmatter.
- `_append_continuity` creates a valid file + section and caps oversize input.
- `_strip_and_mark_capture` on a COPY mutates body + sets `share_status: cleared`, never deletes.
- Watermark write→advance→re-read is monotonic.

### Integration (manual, against a temp copy of `_inbox`)
- `--dry-run --inbox-only` prints personal items + continuity + would-be-cleared captures for `colinas`/`vertik`; no writes.
- Real run on a copy: `MEMORY.md` grows (≤5 KB), `projects/<slug>.md` gains continuity, captures stamped `cleared` with personal stripped; second run = no-op (watermark + `share_status` guard).

### Edge cases
- Empty inbox / no new captures → clean exit 0, no calls.
- Malformed-frontmatter capture → skipped + logged, run continues.
- Capture already `share_status: cleared` → skipped.
- `personal`(Vertik) capture → personal extraction proceeds, stripped, marked; **no LinOS path touched** (assert no writes outside `Memory/MEMORY.md`, `Memory/projects/`, the capture itself).
- `MEMORY.md` byte-size stays ≤ cap after a busy multi-project day.
- `projects/<slug>.md` hand-written header preserved across runs.

---

## VALIDATION COMMANDS

### Level 1: Syntax & import
- `uv run python -c "import ast; ast.parse(open('.claude/scripts/memory_reflect.py').read())"`
- `uv run python -c "import sys;sys.path.insert(0,'.claude/scripts');import memory_reflect"`

### Level 2: Deterministic / dry-run (no SDK)
- `uv run python .claude/scripts/memory_reflect.py --dry-run --inbox-only` → prints parsed per-project JSON
- `git status BrunOS/Memory` → **no changes** after any `--dry-run`

### Level 3: End-to-end (consumes Anthropic tokens — run intentionally, ideally on a copied inbox)
- `uv run python .claude/scripts/memory_reflect.py --inbox-only --project colinas` → `projects/colinas.md` continuity grows; `colinas/` captures stamped `cleared`; re-run = no-op
- Assert `MEMORY.md` ≤ 5120 B (`wc -c BrunOS/Memory/MEMORY.md`)

### Level 4: Index + search round-trip
- `uv run python .claude/scripts/memory_index.py --paths BrunOS/Memory/projects/colinas.md` then `uv run python .claude/scripts/memory_search.py "what's the state of the colinas project" --path-prefix projects`

---

## ACCEPTANCE CRITERIA

- [ ] Reflection ingests per-project inboxes (since a per-project watermark) and consolidates personal knowledge into `MEMORY.md` (≤5 KB preserved).
- [ ] Each processed project gains a maintained `## Auto-consolidated continuity` section in `projects/<slug>.md` (created with valid frontmatter if absent; hand-written header preserved; capped).
- [ ] Each processed capture is rewritten with personal asides stripped and `share_status: cleared` stamped — **never deleted or moved**.
- [ ] No write path outside the BrunOS vault; no `_shared/` folder created; `default_export` preserved, not used to route to LinOS.
- [ ] Idempotent: re-run is a no-op (watermark + `share_status` guard).
- [ ] Recursion guard intact (`CLAUDE_INVOKED_BY=reflection`); every `ClaudeAgentOptions` uses `setting_sources=None`, `allowed_tools=[]`, `max_turns=1`.
- [ ] `--dry-run` writes nothing and advances no watermark.
- [ ] Malformed/empty/already-cleared captures handled without crashing.
- [ ] `CLAUDE.md` + the three federation docs updated to supersede the staging/consolidate design.
- [ ] Phases 5–6 (transport/retirement/consumer) documented as contract, **not implemented**.

## COMPLETION CHECKLIST

- [ ] All Phase 1–4 tasks done in order, each dry-run-validated immediately.
- [ ] No deletions/moves of captures anywhere (strip-in-place only).
- [ ] `MEMORY.md` untouched-in-size-beyond-cap; hand-written `projects/*` headers intact.
- [ ] Manual end-to-end run on a copy confirms 3 outputs + idempotency.
- [ ] `CLAUDE.md` + federation docs updated; phase status noted.

## NOTES

- **Why reflection, not a consolidate pass:** the 2026-05-24 decision cut `memory_consolidate.py` — instant feature-card compaction was low-value; draining inboxes daily into the personal brain is the real need, and reflection already runs daily. Episodic compaction-on-done is dropped; procedural **dreaming → playbook** stays a separate parallel plan.
- **Why no `_shared/` staging:** per-company inboxes + strip-in-place + `share_status: cleared` give the privacy boundary as a flag. The capture is the shared artifact; the company brain reads it directly (LinOS reads only `linos-protostack` inboxes; VertikOS would read only `vertik`). VertikOS can later tighten to a staged/extract model per-destination without re-architecting (different policy, same primitives).
- **Reflection runs on the VPS** (always-on; Mac requires the machine to be up). This means strip-in-place mutates the VPS copy → **Mac→VPS rsync must be `-a --update` (deliver-only), never `--delete`**, or the Mac's raw original clobbers the cleaned VPS copy. Captures are immutable after the flush writes them, so the Mac never needs to re-send a given file.
- **Retirement is VPS-side, not Mac-driven** (deferred): the "fully consumed?" decision needs LinOS's ack manifest, which lives on the VPS alongside BrunOS's watermark. A deterministic VPS job deletes on `BrunOS-processed AND LinOS-acked ELSE 15-day fallback`. **Resurrection guard:** any plain rsync re-adds files the VPS deleted → Mac self-prunes its local inbox (age-based) and/or rsync uses `--exclude-from=<retired-list>`.
- **Cross-VPS production (Bruno's open question, personal brain and work brain on *separate* hosts):** the model gets *cleaner*, not harder. Mac→personal-VPS→work-VPS becomes a constrained host-to-host channel (or a pull by the work brain). The same gates apply — `default_export` tag scopes which work brain may read which inbox, `share_status: cleared` gates what's readable, the deny-by-tag keeps Vertik off LinOS. The ack moves from "same box, different Unix user" to a remote fetch — which is exactly what Phase D's `query(question)→answer` MCP formalizes. **New concerns at that point:** (1) transport auth + encryption between hosts (mTLS / SSH / Tailscale), (2) retirement now spans a network boundary so the ack channel must be reliable (lengthen the fallback, or make retirement strictly ack-gated with a longer TTL), (3) per-tenant ACL on which inboxes a given work brain may pull. None of these change the BrunOS producer built here — they're all consumer/transport-side, which is why the producer is safe to build first.
- **Confidence (one-pass, Phases 1–4 only): 8/10.** The `memory_reflect.py` mirror + deterministic helpers are high-confidence. Soft spots: (a) the strip-in-place LLM faithfully preserving work content while removing only personal asides (budget a prompt-tuning pass; verify on real `colinas` captures), and (b) matching `cleaned_captures[].capture` filenames robustly. Phases 5–6 are deliberately contract-only (blocked on C.5 / LinOS-as-agent).
```