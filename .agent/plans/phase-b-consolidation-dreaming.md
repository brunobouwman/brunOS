# Feature: Phase B — Memory Consolidation + Dreaming (episodic compaction + procedural playbook)

The following plan should be complete, but it's important to validate documentation, codebase patterns, and task sanity before starting implementation. Pay special attention to:

- **This is the long-promised "Phase B."** Phase A (already shipped) captures external-repo sessions into `BrunOS/Memory/_inbox/sessions/<project>/` with routing frontmatter. Phase B was always "promotion out of the inbox." This plan defines that promotion as **two passes on two cadences**, and subsumes the LinOS-promotion half of the original Phase B sketch.
- **Lineage — the dreaming pass IS the PRD's "sleep consolidation."** `.claude/skills/create-second-brain-prd/references/architecture-reference.md:78` frames the memory layer as *"short-term experiences → sleep consolidation → long-term storage"* and attaches that metaphor to **Daily Reflection** (`memory_reflect.py`, which actually runs morning/Sonnet/daily-log-scoped). The metaphor was never given its own mechanism. `memory_dream.py` is that mechanism, finally split out of reflection: nightly, cheap, broad-sweep, procedural-only. Record this rationale in the NOTES section of any follow-on docs.
- **Two memory streams with OPPOSITE lifecycles — do not merge them.** Episodic ("what I worked on") compacts to a reference card when a feature is done. Procedural ("how I work" — patterns, processes, prompting recipes) accumulates monotonically and is NEVER compacted away. If one pass did both, archiving a feature would either eat the patterns or bloat the card. The procedural extraction MUST happen before/independently of episodic archival. Hence two scripts.
- **Decisions locked in conversation 2026-05-23:** Trigger model = **marker + staleness** (explicit "feature done" marker primary; staleness safety net). **PR-merge trigger is DEFERRED** — `integrations.github` has no `merged_prs` (confirmed gap, MEMORY.md Lessons 2026-05-03) and the PR→captures mapping is non-trivial; do NOT build it in this phase. Build scope = **both streams together** (episodic cards + procedural playbook from day one), as two scripts on two cadences.
- **Recursion guard is mandatory in every Agent SDK script.** Both new scripts import `claude_agent_sdk`. Each MUST `os.environ.setdefault("CLAUDE_INVOKED_BY", "<purpose>")` BEFORE the SDK import. Values: `"consolidate"` and `"dream"`. Mirror `memory_reflect.py:19`.
- **`setting_sources` is not optional.** Both scripts are pure-reasoning (no tools, no skills). Every `ClaudeAgentOptions(...)` passes `setting_sources=None`, `allowed_tools=[]`, `max_turns=1`. Mirror `memory_reflect.py:126-132`.
- **NEVER delete (SOUL.md).** Compaction ARCHIVES raw captures (move to `_inbox/sessions/<project>/_archive/`, flip `status: archived`), it does not delete them. The feature card links back to the archive. This is what makes aggressive compaction safe and reversible.
- **Confidentiality is a hard invariant.** A *prompting pattern* learned on Vertik work is safe to keep in `playbook/` (it's "how Bruno works"), but the feature *detail* is Vertik-confidential and must NEVER cross to LinOS (MEMORY.md, daily logs 2026-05-21/22 — "Vertik confidential content must NEVER cross to LinOS"). The dreaming pass MUST (a) respect each capture's `default_export` frontmatter, and (b) when extracting a pattern from a `personal`/Vertik source, strip project-specific identifiers (client names, repo specifics, internal IPs) before it lands in the playbook.
- **MEMORY.md 5KB hard cap — feature cards do NOT go there.** Cards live in `projects/<x>.md` / `clients/<x>.md` ledgers. Playbook entries live in `playbook/`. MEMORY.md stays the top-level durable summary, grown only by `memory_reflect.py`.
- **The LLM never sees raw tokens, and external content is sanitized.** Inbox captures are already-distilled BrunOS content (lower risk than raw integration payloads), but still wrap any content crossing into a prompt with `sanitize.wrap_external(text, label)` — mirror `aggregate_week.py:335-337`.
- **No scheduling in this phase (mirror Phase 6 discipline).** Ship manual CLIs only: `uv run python .claude/scripts/memory_consolidate.py ...` and `... memory_dream.py ...`. launchd plists / systemd timers are a follow-on deploy task (see Phase 9 artifacts in `deploy/`), not part of this phase. Bruno runs both by hand until then.

## Feature Description

BrunOS captures every external-repo agent session (Claude Code + Codex) into a per-project inbox at `BrunOS/Memory/_inbox/sessions/<project>/`. Today nothing consumes that inbox — it grows forever as raw, verbose, per-session distillations. This feature adds the consumption layer as two consolidation passes:

1. **`memory_consolidate.py` (episodic)** — triggered when a feature is *done*. Collects a project's un-compacted inbox captures, compacts them into a single **feature card** appended to the project's ledger (`projects/<x>.md` / `clients/<x>.md`), and archives the raw captures. Triggered explicitly by a **marker** (Bruno signals "done") and, as a safety net, by **staleness** (a project inbox untouched for N days is surfaced for compaction in the weekly review).

2. **`memory_dream.py` (procedural)** — runs nightly on cheap tokens. Sweeps inbox + archive captures since its last watermark, extracts durable *how-I-work* knowledge (patterns, processes, prompting recipes), de-dupes against the existing `playbook/`, and appends genuinely-new entries. This is the substrate that — once dense enough — lets BrunOS automate work the way Bruno does it (the `projects/vertik.md` "agent automating 30–50% of work" 6-month goal).

The two satisfy three distinct user goals: (1) revisit context on a past feature, (2) maintain a chronological index of everything worked on, (3) accumulate an automation-ready model of how Bruno works.

## User Story

As Bruno (Vertik contractor + Protostack co-founder, whose external-repo sessions already land in the BrunOS inbox)
I want finished features compacted to a linkable reference card while their raw detail is archived (or promoted to LinOS for Protostack), and a separate off-hours pass that mines my sessions for reusable patterns and prompting practices
So that my project memory stays an index instead of a write-only dump, I can always revisit a past feature's context, and BrunOS slowly builds an explicit model of how I work that it can later automate from — without ever deleting anything or leaking Vertik detail into the joint LinOS vault.

## Problem Statement

Without Phase B:

1. **The inbox grows unbounded.** `vertik-lab-agent/`, `vertik-studio/`, `colinas/` already hold dozens of raw per-session captures with no lifecycle. There's no "this feature is done, file it" transition.
2. **No project index exists.** There's no single place that answers "what features have I shipped on this project, and where's the context?" — the captures are per-session, not per-feature.
3. **Procedural knowledge is never extracted.** Every session embeds reusable technique (a prompting approach that worked, a debugging process, a migration pattern), but nothing mines it. The automation goal in `projects/vertik.md` has no substrate to build on.
4. **The PRD's "sleep consolidation" was never built.** It got collapsed into morning reflection (`memory_reflect.py`), which is daily-log-scoped and Sonnet-priced — neither the off-hours cadence nor the cross-session sweep the metaphor implies.

Risk of doing it wrong: an irreversible compaction that loses feature detail; Vertik IP leaking into LinOS via a carelessly-promoted pattern; MEMORY.md blown past its 5KB cap by feature cards; a dreaming pass that re-reads everything every night (cost) or misses archived captures (correctness).

## Solution Statement

Two pure-reasoning scripts + a marker mechanism + new vault folders + a weekly-review hookup. Both scripts mirror `memory_reflect.py`'s shape (single SDK call, JSON-out, deterministic apply, idempotent state). Episodic uses Sonnet (summary quality); dreaming uses Haiku (cheap, high volume). PR-merge triggering and scheduler units are explicitly out of scope.

```
.claude/scripts/memory_consolidate.py   # episodic: marker + staleness → feature card + archive (~300 lines)
.claude/scripts/memory_dream.py         # procedural: nightly sweep → playbook (~280 lines)
.claude/scripts/consolidate_common.py   # shared inbox-scan/group/watermark helpers (~150 lines)
BrunOS/Memory/playbook/_README.md       # procedural stream home + entry format
.claude/data/state/consolidation.json   # per-project last-compacted watermark + processed markers
.claude/data/state/dream.json           # last dream watermark + processed capture paths
.claude/skills/weekly-review/scripts/aggregate_week.py  # UPDATE: add "Candidates to compact" section
CLAUDE.md                               # add Phase B commands + a "Memory consolidation (Phase B)" section
```

## Feature Metadata

**Feature Type**: New Capability
**Estimated Complexity**: High (two LLM passes, cross-vault confidentiality routing, lossy-but-reversible compaction, idempotency across two state files)
**Primary Systems Affected**: vault `_inbox/` lifecycle, new `playbook/` + project ledgers, weekly-review, `shared.py` helpers
**Dependencies**: `claude_agent_sdk` (already vendored), `memory_search.py` (Phase 3, subprocess), `sanitize.py` (Phase 6/8). No new pip deps.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — READ THESE BEFORE IMPLEMENTING

- `.claude/scripts/memory_reflect.py` (full, esp. lines 19, 123-138, 156-184, 195-224, 237-278, 281-287, 309-377) — Why: the canonical pattern for BOTH new scripts. `_reason()` SDK-call shape, `_parse_promotions()` tolerant JSON extraction, `_append_promotions()` section-insertion, `_compact_if_over_cap()` compaction guard, idempotent `last_reflection.json` state, `--dry-run` discipline. Mirror all of it.
- `.claude/scripts/shared.py` — Why: every helper both scripts need.
  - `write_inbox_capture()` (307-353) — the EXACT inbox file format the scripts must read (frontmatter fields `project`/`default_export`/`session_id`/`source`/`status`; body = `## Memory flush (HH:MM)` + bullets). Marker files mirror this writer style.
  - `append_to_daily_log()` (218), `load_state()`/`save_state()` (356-360), `atomic_write()` (184), `file_lock()` (142), `now_brt()`/`_ts_brt()` (79-85), `vault_path()` (127), `derive_project_slug_from_path()` (269), `trim_dedup_entries()` (511), `STATE_DIR`/`REPO_ROOT` (20-21).
- `.claude/skills/news-digest/scripts/digest.py` (lines 48, 95-111, 305) — Why: the `memory_search.py` subprocess dedup pattern (`--k 1 --path-prefix <folder>`, parse JSON, threshold on RRF) and the Haiku-model `_reason(..., model=HAIKU_MODEL)` shape for cheap calls. The dreaming pass's playbook dedup mirrors this directly.
- `.claude/skills/weekly-review/scripts/aggregate_week.py` (lines 51-52, 218-239, 303-325, 335-341, 344-374) — Why: (a) the staleness "Candidates to compact" section gets injected here; (b) `_is_refined()`/`DRAFT_MARKER` draft-protection pattern; (c) `wrap_external()` usage; (d) `memory_search.py` subprocess for daily themes.
- `BrunOS/Memory/_inbox/sessions/colinas/2026-05-18-092033-a227e17c.md` — Why: a REAL capture file. Confirm the frontmatter you parse matches on-disk reality (note: this file's `default_export: personal` despite the repo's config intent — trust the file's frontmatter, not assumptions).
- `.claude/scripts/sanitize.py` — Why: `wrap_external(text, label)` signature + `TRUST_BOUNDARY_INSTRUCTION`. All capture content crossing into a prompt gets wrapped.
- `.claude/scripts/memory_search.py` — Why: CLI contract for the dedup subprocess (`--k`, `--path-prefix` with NO trailing slash per MEMORY.md lesson). Confirm output JSON shape (`file_path`, `score`).

### New Files to Create

- `.claude/scripts/consolidate_common.py` — shared helpers: scan a project inbox, read+parse capture frontmatter, group captures, read/advance watermarks, archive a capture (move to `_archive/`, flip status), the marker file format (`read_markers`/`write_marker`/`retire_marker`).
- `.claude/scripts/memory_consolidate.py` — episodic pass. Subcommands: `mark` (write a done-marker), `run` (process markers + staleness).
- `.claude/scripts/memory_dream.py` — procedural pass (nightly sweep → playbook).
- `BrunOS/Memory/playbook/_README.md` — documents the procedural stream + per-entry frontmatter spec (`type: reference`, `category: pattern|process|prompt`, `name`, `when-to-use`, `source-refs`).

### Patterns to Follow

**SDK call (both scripts), mirror `memory_reflect.py:123-138`:**
```python
options = ClaudeAgentOptions(
    allowed_tools=[], setting_sources=None,
    system_prompt=SYSTEM_PROMPT, max_turns=1,
    model=SONNET_MODEL,   # consolidate; memory_dream uses HAIKU_MODEL
)
```

**Recursion guard, mirror `memory_reflect.py:19`:** `os.environ.setdefault("CLAUDE_INVOKED_BY", "consolidate")` (resp. `"dream"`) as the first executable statement, BEFORE `claude_agent_sdk` import.

**Tolerant JSON parse, reuse `memory_reflect.py:_parse_promotions` shape** — both passes emit JSON arrays; copy the fence/`[`-`]`-extraction tolerance verbatim.

**memory_search dedup subprocess, mirror `digest.py:104-111`:** `subprocess.run([python, search_script, query, "--k", "1", "--path-prefix", "playbook"], capture_output=True, ...)`; parse JSON; treat RRF score above a tuned threshold as "already known → skip." NO trailing slash on `--path-prefix`.

**Vault writes:** `with file_lock(path): atomic_write(path, text)` always. Frontmatter on every new file (`type/created/updated/tags/status`, `tags` as block list — see MEMORY.md feedback entries).

**Idempotency, mirror `memory_reflect.py:281-287`:** per-project watermark + processed-marker/capture IDs in a state JSON; re-runs skip already-done work.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation (`consolidate_common.py` + folders)

Build the deterministic substrate both LLM passes sit on — no SDK calls yet, fully unit-testable.

**Tasks:** capture-file parsing (frontmatter → dict), project-inbox scan, capture grouping, watermark read/advance, archive-a-capture (move + status flip, never delete), marker file format (write/read/retire), `playbook/_README.md` + folder seed.

### Phase 2: Episodic pass (`memory_consolidate.py`)

`mark` subcommand (writes a done-marker into the project inbox) + `run` subcommand (Sonnet compacts marked features → card in ledger → archive raw; surfaces staleness candidates to state for the weekly-review hookup). Honor `default_export`: `linos-protostack` detail → staged for LinOS promotion (write to a `_promote/linos/` staging area + manifest line — reuse the staging design from daily logs 2026-05-21); `personal`/Vertik → archive locally; `discard` → archive without carding.

### Phase 3: Procedural pass (`memory_dream.py`)

Nightly sweep of inbox + `_archive/` since `dream.json` watermark → Haiku extracts candidate patterns/processes/prompts as JSON → for each, `memory_search --path-prefix playbook` dedup → append genuinely-new entries as individual `playbook/<slug>.md` files, stripping project identifiers when the source `default_export != linos-protostack`. Advance watermark.

### Phase 4: Integration + Validation

Wire staleness candidates into `aggregate_week.py` ("Candidates to compact" section). Update CLAUDE.md. Dry-run both passes end-to-end against the real (copied) inbox. Confirm idempotency (second run is a no-op).

---

## STEP-BY-STEP TASKS

Execute in order. Each is atomic and independently testable.

### CREATE `.claude/scripts/consolidate_common.py`
- **IMPLEMENT**: `parse_capture(path) -> dict|None` (frontmatter + body); `iter_project_inboxes() -> list[Path]`; `captures_for_project(slug, since_ts=None) -> list[Path]`; `read_watermark(state_path, key)` / `advance_watermark(...)`; `archive_capture(path)` (move to `_inbox/sessions/<project>/_archive/`, set `status: archived` via frontmatter rewrite, `file_lock`+`atomic_write`, **never `unlink`**); `write_marker/read_markers/retire_marker` (marker = `_inbox/sessions/<project>/_markers/<ts>-<hash>.md`, frontmatter `type: system`, fields `project/feature/pr_url/created/status: pending`).
- **PATTERN**: frontmatter writer style from `shared.py:write_inbox_capture` (334-352); state from `shared.py:load_state/save_state`.
- **IMPORTS**: `from shared import vault_path, now_brt, _ts_brt, file_lock, atomic_write, load_state, save_state, _slug`.
- **GOTCHA**: `_slug` is private but already imported cross-module by `codex_rollout.py:44` — acceptable. Archive moves MUST be lock-guarded; never delete source.
- **VALIDATE**: `uv run python -c "import sys; sys.path.insert(0,'.claude/scripts'); import consolidate_common as c; print(c.parse_capture(__import__('pathlib').Path('BrunOS/Memory/_inbox/sessions/colinas/2026-05-18-092033-a227e17c.md'))['project'])"` → prints `colinas`.

### CREATE `BrunOS/Memory/playbook/_README.md`
- **IMPLEMENT**: frontmatter (`type: reference`, tags block-list, status active) + entry-format spec: per-pattern file `playbook/<slug>.md` with `category: pattern|process|prompt`, `name`, `when-to-use`, body = the technique, `source-refs` (scrubbed of confidential identifiers).
- **PATTERN**: folder `_README.md` style from `BrunOS/Memory/_inbox`/other folder READMEs.
- **GOTCHA**: `memory_index.py` will index this folder → searchable. Ensure no `personal/finance.md`-style exclusion needed (playbook is safe).
- **VALIDATE**: `uv run python .claude/scripts/memory_index.py --paths BrunOS/Memory/playbook/_README.md --dry-run` exits 0.

### CREATE `.claude/scripts/memory_consolidate.py`
- **IMPLEMENT**: `os.environ.setdefault("CLAUDE_INVOKED_BY","consolidate")` first; argparse subcommands `mark` (`--project --feature [--pr]` → `write_marker`) and `run` (`--dry-run`, `--stale-days N` default 10). `run`: for each pending marker, gather that project's un-compacted captures (since watermark, up to marker time) → `wrap_external` join → Sonnet `_reason` with a card-summary system prompt → parse → append card to `projects/<slug>.md` or `clients/<slug>.md` (create with frontmatter if absent) → archive captures per `default_export` routing → retire marker → advance watermark. Then compute staleness candidates → `save_state(consolidation.json, ...)`.
- **PATTERN**: SDK call `memory_reflect.py:123-138`; section-append `memory_reflect.py:195-224`; idempotency `memory_reflect.py:281-287`.
- **IMPORTS**: `consolidate_common`, `from shared import vault_path, now_brt, file_lock, atomic_write, load_state, save_state`, `from sanitize import wrap_external`.
- **GOTCHA**: ledger may not exist → create with proper frontmatter (`type: project`/`client`). Card goes to ledger, NEVER MEMORY.md. `linos-protostack` detail → staging dir + manifest, not a direct LinOS write (LinOS is a separate vault/repo).
- **VALIDATE**: `uv run python .claude/scripts/memory_consolidate.py run --dry-run` prints would-be cards + candidates, writes nothing.

### CREATE `.claude/scripts/memory_dream.py`
- **IMPLEMENT**: `os.environ.setdefault("CLAUDE_INVOKED_BY","dream")` first; argparse `--dry-run`, `--since-days N`. Sweep `_inbox/**` + `_archive/**` captures created after `dream.json` watermark → `wrap_external` join (respect/skip `discard`) → Haiku `_reason` emitting JSON `[{category, name, when_to_use, technique, identifiers_present: bool}]` → for each: `memory_search --path-prefix playbook --k 1`; if top RRF < threshold (new), write `playbook/<slug>.md`; if source not `linos-protostack`, instruct the model (and verify) that identifiers are stripped. Advance watermark.
- **PATTERN**: Haiku call `digest.py:305`; dedup subprocess `digest.py:95-111`; JSON parse `memory_reflect.py:_parse_promotions`.
- **IMPORTS**: as above + `subprocess`, `from shared import REPO_ROOT`.
- **GOTCHA**: MUST read `_archive/` too (episodic pass archives at marker time; dreaming runs later) — or it misses compacted detail. Watermark prevents re-reading nightly. De-dup BEFORE writing to keep playbook clean.
- **VALIDATE**: `uv run python .claude/scripts/memory_dream.py --dry-run --since-days 15` prints candidate playbook entries + dedup verdicts, writes nothing.

### UPDATE `.claude/skills/weekly-review/scripts/aggregate_week.py`
- **IMPLEMENT**: add a `_gather_compaction_candidates()` reading `consolidation.json` → a "## Candidates to compact" section in the bundle so the Opus review surfaces stale project inboxes for Bruno's yes.
- **PATTERN**: section-gather functions `aggregate_week.py:328-341`.
- **GOTCHA**: read-only of state; don't trigger compaction from the review.
- **VALIDATE**: `uv run python .claude/skills/weekly-review/scripts/aggregate_week.py --dry-run` includes the new section.

### UPDATE `CLAUDE.md`
- **IMPLEMENT**: new "## Memory consolidation (Phase B)" section documenting both scripts, the two-stream model, the marker/staleness triggers, the never-delete/archive rule, the confidentiality-strip rule, and the dreaming↔"sleep consolidation" lineage. Add the build commands. Flip the relevant phase-status line.
- **VALIDATE**: `grep -c "memory_dream.py" CLAUDE.md` ≥ 1.

---

## TESTING STRATEGY

There is no test suite in this repo today (no `tests/`, no pytest config in `pyproject.toml`). Match the project's actual validation idiom: **`--dry-run` flags + targeted manual runs against a copied inbox**, exactly how Phases 5–6 were validated (`--dry-run`/`--no-agent`/`--force`).

### "Unit" checks (dry-run, no SDK)
- `consolidate_common.parse_capture` round-trips a real capture's frontmatter.
- `archive_capture` on a COPY moves the file and flips status, leaving no deletion.
- Marker write→read→retire cycle.

### Integration (manual, against a temp copy of `_inbox`)
- `memory_consolidate.py mark --project colinas --feature "x"` then `run --dry-run` shows the card.
- `memory_dream.py --dry-run` extracts patterns and correctly de-dupes a deliberately-duplicated one.
- Second `run`/dream invocation = no-op (idempotency via watermark).

### Edge Cases
- Empty inbox / no markers → clean exit 0.
- Capture with malformed frontmatter → skipped, logged, not crashed.
- `default_export: discard` capture → archived, never carded, never mined.
- Vertik (`personal`) pattern → playbook entry has identifiers stripped; no LinOS staging write.
- MEMORY.md untouched by either pass (assert byte-size unchanged).
- Ledger doesn't exist yet → created with valid frontmatter.

---

## VALIDATION COMMANDS

Run all; expect zero errors and no unintended vault writes during dry-runs.

### Level 1: Syntax & Import
- `uv run python -c "import ast,glob; [ast.parse(open(f).read()) for f in ['.claude/scripts/memory_consolidate.py','.claude/scripts/memory_dream.py','.claude/scripts/consolidate_common.py']]"`
- `uv run python .claude/scripts/memory_consolidate.py run --dry-run` (imports resolve, no SDK call needed to parse args)

### Level 2: Deterministic behavior (no SDK)
- `consolidate_common` parse/archive/marker checks above.
- `git status BrunOS/Memory` shows NO changes after any `--dry-run`.

### Level 3: End-to-end (consumes Anthropic tokens — run intentionally)
- `uv run python .claude/scripts/memory_consolidate.py mark --project colinas --feature "test card"`
- `uv run python .claude/scripts/memory_consolidate.py run` → card appended to `projects/`/`clients/`, captures moved to `_archive/`, marker retired.
- `uv run python .claude/scripts/memory_dream.py --since-days 15` → new `playbook/*.md`; re-run = no-op.

### Level 4: Index + search round-trip
- `uv run python .claude/scripts/memory_index.py` then `uv run python .claude/scripts/memory_search.py "how Bruno approaches X" --path-prefix playbook` returns the new entries.

---

## ACCEPTANCE CRITERIA

- [ ] `memory_consolidate.py mark` + `run` compacts a marked feature's captures into one ledger card and archives the raw (zero deletions — verify files exist in `_archive/`).
- [ ] Feature cards land in `projects/`/`clients/` ledgers, NEVER in MEMORY.md (MEMORY.md byte-size unchanged across runs).
- [ ] `memory_dream.py` extracts procedural entries into `playbook/`, de-duped against existing entries; re-run is a no-op.
- [ ] `default_export` routing honored: `linos-protostack` detail staged (not direct LinOS write), `personal`/Vertik archived locally with identifiers stripped from any derived playbook entry, `discard` archived without carding/mining.
- [ ] Both scripts: recursion guard set before SDK import; `setting_sources=None`; `--dry-run` writes nothing.
- [ ] Staleness candidates surface in the weekly-review draft.
- [ ] Idempotent across two state files (`consolidation.json`, `dream.json`).
- [ ] CLAUDE.md documents both passes + the lineage; no scheduler units added (deferred).
- [ ] All Level 1–2 validations pass; Level 3–4 confirmed manually.

## COMPLETION CHECKLIST

- [ ] All tasks completed in order, each dry-run-validated immediately.
- [ ] No deletions anywhere (archive-only confirmed).
- [ ] No Vertik identifier leakage into `playbook/` or LinOS staging.
- [ ] MEMORY.md untouched by both passes.
- [ ] Manual end-to-end run confirms cards + playbook entries + idempotency.
- [ ] CLAUDE.md + phase status updated.

## NOTES

- **Why two scripts not one:** episodic and procedural memory have opposite lifecycles (compact-on-done vs accumulate-forever). One pass would force a bad coupling. Splitting by cadence (event-triggered vs nightly) also lets the cheap broad sweep see *cross-feature* patterns the per-feature pass can't.
- **The dreaming lineage:** `architecture-reference.md:78` ("sleep consolidation") was the PRD's metaphor for reflection; this phase gives it a real mechanism. Worth a one-line nod in the CLAUDE.md section.
- **Deferred deliberately:** (1) PR-merge trigger — needs `integrations.github.merged_prs` (doesn't exist) + a PR→captures mapping heuristic; revisit once the marker+staleness path is proven. (2) Anthropic **Batch API** for the dreaming pass — 50% cheaper, async, ideal for nightly, but don't build async batch plumbing for personal-scale volume on day one; start with synchronous Haiku. (3) launchd/systemd units — a follow-on deploy task mirroring `deploy/` Phase 9 artifacts.
- **Open question for Bruno:** is `playbook/` strictly BrunOS-personal (Bruno's how-I-work, feeding HIS automation), or should joint Protostack *process* patterns also flow to LinOS? Default assumption in this plan: BrunOS-local. Flag at execution time if a Protostack process pattern looks LinOS-bound.
- **Confidence score (one-pass implementation): 7/10.** Deterministic substrate + the `memory_reflect.py` mirror are high-confidence. The two soft spots: the `linos-protostack` staging/manifest path (depends on the still-partly-designed Phase B→LinOS promotion from the 2026-05-21 daily logs — may need a decision before Task 3), and the Haiku identifier-stripping for confidential sources (verification-after-generation is inherently fuzzy; budget a tuning pass on the dream system prompt).
