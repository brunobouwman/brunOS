# Data Flows — for root-cause tracing (symptom → cause)

> The end-to-end sequences, with the functions, gates, and state files at each hop. Use
> this in **Phase 4** to trace a failing check back to its upstream cause — a failing
> *signal* is usually a *symptom* of a break one or two hops upstream.
>
> **Synced from** the BrunOS vault: `Memory/projects/Brain/architecture/03-data-flows.md`.

For each flow: the hop chain, the state file at each hop, and the **"if X is broken,
look upstream at Y"** tracing hints.

---

## Flow 1 — Federation (capture → cleared → consumed → acked → retired)

The privacy-critical path. Six stages, two hosts, three one-way rsync legs, never a shared writer.

```
Session/Comms → memory_flush.py → _inbox/sessions/<slug>/  (share_status unset)
  → memory_reflect.py inbox stage  [strip · scrub_secrets · scrub_excluded_entities · FAIL-CLOSED]
  → rewrite + share_status:cleared
  → sync_cleared_inbox.py  [DUAL GATE: validate_consumer_read(scope) AND share_status==cleared]
  → /home/linos/brunos-inbox/  (rsync -a --update, no --delete)
  → linos_consumer.py  [dual gate AGAIN, read-only] → joint/ + LINMEMORY + ack manifest
  → sync_acks.py  → /home/bruno/linos-acks/  (rsync --ignore-existing)
  → retire_vps_inbox.py  [processed AND acked → retire; ELSE 15-day fallback; quarantined → never]
```

**State at each hop:** capture frontmatter `share_status` · `inbox_reflection.json`
(per-project clear watermark) · `linos-inbox-sync-state.json` · `consumer_watermark.json`
(per-slug) · `_acks/brunos/*.json` · `linos-ack-sync-state.json` · `retired_inbox.json`.

**Tracing hints:**
- **Captures not clearing (G1/G6)?** → look upstream at `memory_reflect.py` inbox stage:
  is the clear watermark advancing (`inbox_reflection.json`)? Is `_excluded-people.md`
  loadable (a deny-list load failure makes the scrub **fail-closed** → refuses to clear →
  pile-up)? After 3 failed attempts a capture force-`quarantined` — a growing quarantine
  set (G6) means stripping keeps failing.
- **LinOS not consuming (H1/H3)?** → is the cleared **push** running (G5,
  `linos-inbox-sync-state.json`)? Nothing reaches the mirror if the dual gate rejects
  everything (most `default_export: personal` captures are cleared but out-of-scope — that's
  correct, not a bug). Then check the consumer watermark + timer.
- **Captures stuck `awaiting-ack` forever (H4)?** → the ack RETURN leg (`sync_acks.py`,
  09:30) is the classic break — without it the consumer's acks never leave linos's `0700`
  home, so `retire_vps_inbox.py` can never see them. Check `/home/bruno/linos-acks/brunos/`.
- **A privacy-gate ❌ (G2/G3/H2)** is a release-blocker — trace it to the exact enforcement
  site (`sync_cleared_inbox.py` `_is_eligible`, `linos_consumer.py` `_eligible_captures`,
  `tests/test_privacy_gate.py`), never to a symptom.

---

## Flow 2 — Security gates (where each layer fires)

```
Tool call → block-secrets.py (path/bash) → dangerous-bash.py (Bash, exit 2) → protect-soul.py (Edit/Write) → run
External content → sanitize.wrap_external (strip injection, redact base64, escape XML) → prompt
Capture → scrub_secrets + scrub_excluded_entities → share_status:cleared
```

**Tracing hints:**
- **I1 ordering wrong** → `settings.json` hook array order must be block-secrets →
  dangerous-bash → protect-soul. A reorder silently weakens the chain.
- **I5/G3 canary red** → `tests/test_privacy_gate.py` plants fake secrets/PII/excluded
  names and asserts none survive clearing. A red canary means the scrub regexes or the
  fail-closed status handling regressed — **release-blocker**, fix before any federation.

---

## Flow 3 — Heartbeat tick (proactive loop, individual role)

```
systemd timer (30min, 08–22 BRT) → heartbeat.py
  1 reindex (memory_index.py)
  2 gather (asyncio.gather, return_exceptions) — slack/github/clickup/gmail/calendar/rss
  3 build snapshot + diff vs prior; PERSIST snapshot BEFORE agent (crash-safe)
  4 drafts expiry + habits + gap scan + decision loop
  5 [empty delta → fast-path 1-line tick] ELSE Haiku guardrail (default-deny) → Sonnet agent → notify
```

**State:** `heartbeat-state.json` (snapshot) · `heartbeat-monitor-state.json` (tick outcomes).

**Tracing hints:**
- **D1 heartbeat not ticking** → is the timer enabled + BRT-pinned (J2/J3)? Is the host the
  primary (not a failover where it's correctly `Disabled`)?
- **D2 erroring** → `heartbeat-monitor-state.json` `consecutive_failures` + the failure
  class: `guardrail-block` storm (injection or a noisy delta tripping Haiku), `gather-error`
  (an integration down → trace to E2), or `agent-error`.
- **Stale snapshot but timer green** → step-1 reindex may be failing (trace to B1/B2) or
  step-2 gather hanging on one integration.

---

## Flow 4 — Chat session (Slack remote surface)

```
DM / @mention → bot.py (Socket Mode) → SessionManager.get_or_create (resume from chat.db)
  → ClaudeSDKClient per thread → reply in-thread
  reaper every 5min: idle 60min → reap · LRU cap 4 → flush transcript (incremental watermark)
```

**State:** `slack-state.json` (bot_user_id) · `chat.db` (thread index, session_ids) ·
`flush_offsets.json` (incremental flush watermark).

**Tracing hints:**
- **F2 duplicate replies (release-blocker)** → Socket Mode is fan-out broadcast: TWO
  `bot.py` processes = double replies. Trace to J6 single-instance — a failover that
  started the Mac bot without stopping the VPS one. `slackbot_watchdog.py` detects this.
- **Bot down (F1)** → `bot.py --smoke-test` (auth.test). Token rotated? Unit crashed
  (check restart-storm via watchdog)?
- **Chat knowledge not landing in memory** → the flush-on-close path (`dispatch_flush`,
  `_incremental`); check `flush_offsets.json` advancing + the 2KB floor.

---

## Flow 5 — Dreaming + decision-rationale loop

```
nightly 03:00 → memory_dream.py → gather captures > watermark
  [< trigger_min_captures → log + exit, NO model call]  ELSE
  Haiku extract processes/decisions → dedup vs playbook (fail-open) → playbook/ (scrubbed)
  low-confidence decision → provisional entry + enqueue question
heartbeat stage 4c → --deliver-questions (rate-limited) → notify_adapter → person replies → --reconcile folds answer in
```

**State:** `dream.json` (watermark) · `decision_questions.json` (queue).

**Tracing hints:**
- **C3 dreaming "not running"** → first check the **adaptive gate**: fewer than
  `trigger_min_captures` new captures is a *legitimate skip* (log + exit 0, no model call),
  NOT a failure. Only flag if captures exist above the threshold and the watermark didn't move.
- **C4 playbook empty on a brain with history** → either dreaming never ran, or dedup is
  over-aggressive — check `dream.json` watermark vs capture timestamps.

---

## Cross-flow: the most common root causes

| Symptom (checklist) | Usual upstream root cause |
|---|---|
| B4 search canary fails | B2 stale index ← B1 corrupt db ← J5 wedged vault-sync |
| G1 captures not clearing | `_excluded-people.md` unloadable (G4) → scrub fail-closed → pile-up |
| H4 awaiting-ack forever | ack return leg (`sync_acks.py`) not running |
| F2 duplicate replies | J6 dual-run (failover started Mac bot, VPS bot still up) |
| D1 heartbeat stale | J2 timer disabled / J3 not BRT-pinned / B-layer reindex failing |
| Many ⚠️ at once | a sync wedged (J5) → vault/index/everything downstream goes stale |
