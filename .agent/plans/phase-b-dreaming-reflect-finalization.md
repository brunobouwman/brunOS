# Feature: Phase B — Dreaming + Reflect Finalization (modular cadence)

_Draft for review — refine before implementing. Build one task at a time; dry-run-validate each._

> **Supersedes** `.agent/plans/phase-b-consolidation-dreaming.md`. That plan's episodic
> `memory_consolidate.py` is **CUT and stays cut** (2026-05-24 decision, `linos-federation-write-side.md:5`
> — instant feature-card compaction judged low-value; inbox-draining already folded into reflection).
> This plan keeps the *procedural* `memory_dream.py` pass, adds **decision extraction + a rationale
> feedback loop**, finalizes `memory_reflect.py` (the deferred churn/eviction fixes), and makes every
> cadence + behavior **modular per brain** (individual vs company) with sensible defaults.

Decisions locked in conversation 2026-06-06 (Bruno):
- **Cadence = split + adaptive** (default). Inbox/clear runs frequently (federation-fast); personal
  MEMORY curation daily; dreaming nightly + adaptive.
- **Reflect & dream = two passes, co-scheduled** — separate prompts/destinations (knowledge vs
  procedure), shared capture-scan + watermark infra, one nightly entry point (no 2nd model spin-up).
- **Consolidation is NOT reintroduced.** Reflection is the sole knowledge-curation pass; dreaming is
  the sole procedure/decision-extraction pass. They read the same captures, extract orthogonal things,
  write to different places — not duplicative, so no "double cost" on the same work.
- **Config store = `brain-config.json` in state** (stdlib via `shared.load_state`; code defaults so an
  absent file == documented default). Onboarding writes it + generates timer units; scripts read behavior toggles at runtime.
- **Dream adaptive trigger = ≥ N new captures** since last dream watermark (default N=5); else skip.
- **Decision-rationale prompts go through a pluggable notification adapter** (default = Slack chat bot;
  companies plug WhatsApp/Telegram/Teams/email). The surface is NOT hardcoded.

## gbrain reference (confirmed 2026-06-06)
gbrain's "consolidation" is a single nightly *dream cycle* (`sync→extract→embed→consolidate→synthesize`)
with depth **scaled to backlog** ("healthy brains sleep 60 min between ticks; unhealthy get the full
overnight cycle"). Validates (a) the adaptive trigger and (b) NOT running separate reflect+consolidate
passes. Its "synthesize" step ≈ our two-zone compiled-truth pages (ClickUp 86ca1z882) — noted as an
optional extension (B.5), not core here.

---

## Problem statement

1. **Dreaming is unbuilt.** `memory_dream.py` does not exist on any branch. Procedural knowledge
   (how Bruno works) and decision rationale (why he decided) are never extracted — the automation
   substrate (`projects/vertik.md` "agent automating 30–50% of work" goal) has nothing to build on.
2. **Reflect has deferred churn/eviction debt** (flagged daily 2026-06-02; tracked in this Bruno-memory
   "dream PR scope"): MEMORY.md cap-guard compacts on nearly every inbox batch (6796→7004→7764→9712→
   6765→6928 B), so durable items get silently squeezed; the per-batch compaction multiplies cost.
   Fixes: **compact/evict once per run**, **evict-to-archive (never silently drop)**, **separate
   inbox-personal items from daily-stage promotions**. (8 KB cap raise already landed PR #14.)
3. **Nothing is modular.** Cadence + behavior are hardcoded / implicit. For the BaaS product, a company
   must be able to set intervals and what each pass does — with a working default.

## Solution — pipeline (defaults; per-brain override via brain-config.json)

```
CAPTURE  (real-time SessionEnd/PreCompact hooks — exists)
   │
   ▼
INBOX PASS  — frequent (default hourly, work-hours 08-20; adaptive: only projects w/ new captures)
   │   ONE Sonnet call/project, ONE read of the full capture → three outputs:
   ├─ cleaned_captures → strip-in-place + share_status:cleared → company inbox      [federation-fast]
   ├─ personal items   → PENDING BUFFER (personal_pending.json) — NO MEMORY write/compact here
   └─ continuity        → projects/<slug>.md (## Auto-consolidated continuity)
   │
   ▼
MEMORY CURATION  — daily (default 08:00)
   │   drain personal buffer + daily-log promotions → MEMORY.md
   └─ EVICT-TO-ARCHIVE once per run (deterministic: oldest dated bullets → Memory/_archive/MEMORY-archive.md)
   │
   ▼
DREAMING  — nightly (default 03:00); adaptive (skip if < N new captures); Haiku; co-scheduled infra
   ├─ processes/patterns/prompts → playbook/<slug>.md   (automation substrate)
   ├─ decisions → playbook/ entries {decision, context, rationale, alternatives, reversal, confidence}
   └─ low-confidence rationale → QUESTION QUEUE → notify adapter → person → answer folded back into entry
```

**Why no info-loss from frequent clearing:** personal extraction and clearing happen in the *same read*
of the full (un-stripped) capture (today's `_process_inbox_batch`). We never strip-before-curate. The
only change is *where personal items land* (buffer, not inline MEMORY.md) and *when MEMORY is written*
(once daily) — which is exactly the deferred "separate inbox-personal from daily-stage" + "compact once
per run" fix.

---

## Files

**New code**
```
.claude/scripts/brain_config.py        # load brain-config.json + DEFAULTS (stdlib; lru_cached)
.claude/scripts/memory_dream.py        # procedural + decision dreaming → playbook (Haiku, adaptive)
.claude/scripts/notify_adapter.py      # pluggable "ask the person" interface; SlackAdapter default
.claude/scripts/gen_schedules.py       # emit launchd/systemd units from brain-config (cadence → timers)
```
**New vault**
```
BrunOS/Memory/playbook/_README.md                      # procedural+decision stream home + entry schema
BrunOS/Memory/_archive/MEMORY-archive.md               # evicted durable MEMORY.md items (searchable)
BrunOS/Memory/Brain/brain-config.template.json         # onboarding template (documented defaults)
```
**New state**
```
.claude/data/state/brain-config.json        # this brain's cadence + behavior (defaults if absent)
.claude/data/state/personal_pending.json    # buffered personal items awaiting daily curation
.claude/data/state/dream.json               # dream watermark + processed capture ids
.claude/data/state/decision_questions.json  # rationale-prompt queue
```
**Modified**
```
.claude/scripts/memory_reflect.py   # buffer personal (not inline MEMORY); add memory-curation stage;
                                     #   evict-to-archive; config-gate stages + cadence
CLAUDE.md                           # Phase B section, new state files, modular-cadence + brain-config docs
deploy/{launchd,systemd}/           # split inbox-pass / memory-curation / dream units (generated)
```

---

## brain-config.json — schema + DEFAULTS (in `brain_config.py`)

```jsonc
{
  "role": "individual",                  // "individual" | "company"
  "reflection": {
    "inbox_pass":      { "enabled": true,  "cadence": "hourly", "hours": "08-20" },
    "memory_curation": { "enabled": true,  "cadence": "daily@08:00" },
    "federation":      true               // strip+clear+forward (false for solo brains)
  },
  "dreaming": {
    "enabled": true,
    "cadence": "nightly@03:00",
    "trigger_min_captures": 5,            // adaptive: skip the sweep below this
    "extract": ["processes", "decisions"],
    "decision_prompts": { "enabled": true, "max_per_day": 3, "confidence_threshold": 0.6 }
  },
  "notify": { "adapter": "slack", "target": null }   // adapter ∈ {slack, none, ...}; null target = default DM
}
```
- `brain_config.get()` returns DEFAULTS deep-merged with the file (absent file → pure defaults).
- Behavior toggles are read **at runtime** by reflect/dream. Cadence strings are consumed only by
  `gen_schedules.py` (which emits the timer units); the scripts themselves don't schedule.
- Company role flips defaults later (tighter draft-by-default surface, company routines) — out of scope
  here beyond reading `role`; the company-brain agent is a separate track.

---

## IMPLEMENTATION PLAN (phased; each phase dry-run-validated)

### Phase B.0 — Foundation: `brain_config.py` + folders
- `brain_config.py`: `DEFAULTS` dict + `get(path=None)` deep-merge over `load_state(STATE_DIR/"brain-config.json", {})`.
- Create `playbook/_README.md` (entry schema below) + `_archive/MEMORY-archive.md` (frontmatter, empty) + `Brain/brain-config.template.json`.
- **VALIDATE:** `uv run python -c "import sys;sys.path.insert(0,'.claude/scripts');import brain_config as c;print(c.get('dreaming.trigger_min_captures'))"` → `5`.

### Phase B.1 — Reflect finalization (the deferred fixes)
1. **Buffer personal items.** In `_process_inbox_batch` ([memory_reflect.py:927-939](.claude/scripts/memory_reflect.py:927)), replace the inline `_append_promotions`+`_compact_if_over_cap`+MEMORY write with `_buffer_personal(appendable, slug)` → append to `personal_pending.json`. Remove `mem_over_cap` from this path (no MEMORY write here anymore).
2. **Memory-curation stage.** New `_run_memory_curation_stage(dry_run)`: drain `personal_pending.json` + the daily-log promotions (fold the existing `_run_daily_stage` MEMORY-write tail into it), `_append_promotions` once, then `_evict_to_archive_if_over_cap` ONCE. Clear the buffer on success.
3. **Evict-to-archive (deterministic, zero-LLM).** New `_evict_to_archive_if_over_cap(memory_text, cap)`: while over cap, peel the **oldest dated bullet** (`- **YYYY-MM-DD** —`) from the largest section, append it to `Memory/_archive/MEMORY-archive.md` (lock+atomic), until under cap. Replaces silent LLM-squeeze as the primary mechanism; keep `_compact_if_over_cap` available as an optional secondary "merge redundant" pass (off by default). Lossless + cheap.
4. **Config-gate** stages: skip inbox-pass / memory-curation / federation per `brain_config`.
- **VALIDATE:** `memory_reflect.py --dry-run --inbox-only` shows personal items routed to buffer (not MEMORY); `--dry-run` curation prints would-evict bullets; `git status BrunOS/Memory` clean after dry-run. Add `tests/test_reflect_eviction.py` (over-cap doc → oldest bullet lands in archive, MEMORY under cap, nothing deleted).

### Phase B.2 — Dreaming: `memory_dream.py` (processes + decisions)
- Recursion guard `CLAUDE_INVOKED_BY=dream` BEFORE sdk import; `ClaudeAgentOptions(allowed_tools=[], setting_sources=None, max_turns=1, model=HAIKU)`.
- **Adaptive gate:** count captures (inbox + `_archive/`) with `created > dream watermark`; if `< trigger_min_captures` → log + exit 0 (skip).
- Sweep those captures → Haiku → JSON `[{kind: "process"|"pattern"|"prompt"|"decision", ...}]`:
  - process/pattern/prompt → `{category, name, when_to_use, technique, identifiers_present}`
  - **decision** → `{decision, context, inferred_rationale, confidence: 0..1, alternatives, reversal_conditions, source_refs}`
- **Dedup** vs `playbook/` via `memory_search.py --path-prefix playbook --k 1` (RRF threshold) — mirror `digest.py`.
- **Confidentiality:** if source `default_export != linos-protostack`, instruct + verify project identifiers stripped before writing.
- Write new entries as `playbook/<slug>.md`; advance `dream.json` watermark.
- **VALIDATE:** `memory_dream.py --dry-run --since-days 15` prints candidate entries + dedup verdicts + skip-vs-run decision; re-run = no-op.

### Phase B.3 — Decision-rationale feedback loop
- In B.2, a `decision` with `confidence < threshold` → write a **provisional** playbook entry (`rationale: inferred (low-confidence)`) AND enqueue `{id, decision, question, source_refs, confidence, asked:false, answered:false}` in `decision_questions.json`.
- `notify_adapter.py`: `NotifyAdapter.ask(question, ref_id) -> bool`; `SlackAdapter` (default) sends a DM via the chat-bot's `chat:write` (reuse `query.py slack send` / bot client); `NoneAdapter` no-ops. Adapter chosen by `brain_config notify.adapter`. **This is the per-company comms seam.**
- A delivery step (default: piggyback the morning heartbeat, rate-limited to `max_per_day`) pulls unanswered questions → `adapter.ask(...)` → mark `asked`.
- **Answer reconciliation:** the person's reply is captured via the normal chat→flush path; a daily reconciliation matches answers back to `decision_questions.json` by ref_id, patches the playbook entry (fill rationale, raise confidence, mark answered). v1: simple ref-id match in the reply; flag fuzzy matching as the tuning risk.
- **VALIDATE:** seed a low-confidence decision → dry-run shows queued question; `NoneAdapter` path asserts no send; Slack path gated behind `notify.adapter == "slack"`.

### Phase B.4 — Modular cadence wiring
- `gen_schedules.py`: read `brain-config.json` cadence strings → emit launchd plists (Mac) / systemd timers (VPS) for inbox-pass, memory-curation, dream. Idempotent; `--dry-run` prints units.
- Split the current single `*-reflect` unit into `*-reflect-inbox` (hourly) + `*-reflect-curate` (daily) + `*-dream` (nightly). Keep co-scheduled infra (shared `_run` entry with stage flags).
- Document in CLAUDE.md; onboarding (Group 3) will call `gen_schedules.py` — here we just ship it + the default units.
- **VALIDATE:** `gen_schedules.py --dry-run` emits correct `OnCalendar=`/`StartInterval` from defaults; units pass `systemd-analyze verify` (VPS) / `plutil -lint` (Mac).

### Phase B.5 (OPTIONAL / noted) — two-zone compiled-truth pages
gbrain "synthesize" analog: entity pages get a rewritten *State/Assessment* block + append-only *cited
timeline*. Folds naturally into the curation/dream output. Not built here — ClickUp 86ca1z882, after B.1–B.4.

---

## playbook entry schema (`playbook/_README.md`)
```yaml
---
type: reference
category: process | pattern | prompt | decision
name: <short-name>
when-to-use: <trigger>
confidence: high | low            # decisions only; low → has an open rationale question
source-refs: [<scrubbed refs>]    # identifiers stripped if source != linos-protostack
created: ...
updated: ...
tags: [playbook, <category>]
status: active
---
<the process steps / pattern / prompt recipe / decision + rationale + alternatives + reversal>
```

## Invariants (carry through every task)
- **NEVER delete** (SOUL.md): eviction = move to `_archive/`; archival = move to `_inbox/.../_archive/`. Reversible.
- **Confidentiality:** respect each capture's `default_export`; strip project identifiers before any
  Vertik-derived pattern lands in `playbook/`; never cross Vertik detail to LinOS.
- **Recursion guard** (`dream`) + `setting_sources=None` + `allowed_tools=[]` in every new SDK script.
- **`sanitize.wrap_external`** on all capture content entering a prompt.
- **`--dry-run` writes nothing**; idempotent via watermarks; reporting only at the CLI boundary (mirror `memory_reflect`'s `SyncReporter`).

## Acceptance criteria
- [ ] Inbox pass buffers personal items; MEMORY.md is written/evicted ONCE per day (no per-batch churn); byte-size stable across the day.
- [ ] Over-cap MEMORY.md evicts oldest bullets to `_archive/MEMORY-archive.md` (zero deletions; archive grows).
- [ ] No info loss: personal knowledge from a capture survives even though the capture was cleared hourly (extracted in the same read).
- [ ] `memory_dream.py` extracts processes + decisions to `playbook/`, deduped; skips when `< trigger_min_captures`; re-run no-op.
- [ ] Low-confidence decisions enqueue a rationale question; default Slack adapter delivers; answer folds back into the entry; `NoneAdapter` sends nothing.
- [ ] Every cadence + behavior reads from `brain-config.json` with working defaults when absent.
- [ ] `gen_schedules.py` emits valid split units from the config.
- [ ] Confidentiality + recursion-guard + dry-run invariants hold; CLAUDE.md updated.

## Notes / open tuning risks
- Answer→question reconciliation (B.3) is the fuzzy part — v1 ref-id match; budget a tuning pass.
- `confidence_threshold` (0.6) and `trigger_min_captures` (5) are starting defaults — tune against real volume.
- Company-brain role only *read* here (`role` field); the company agent's SOUL/routines/Judge + the
  onboarding wizard that writes brain-config are separate tracks (Group 2/3).
