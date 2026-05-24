# Deferred follow-ups (mirror BrunOS's own roadmap)

These are deferred on BrunOS too, so they are **not parity gaps** — list them in the PRD's
"Deferred follow-ups" section with the same blocked-on notes. Mark whether each is
**per-brain** (this brain builds its own) or **joint** (built once on LinOS, serves both).

The shared sequencing reality: the producer side (contract C1–C10) is buildable now, but the
**end-to-end loop is blocked until LinOS becomes an agent node** (Phase C.5). Don't over-build
ahead of what can read the output.

| # | Follow-up | Scope | Blocked on | Notes |
|---|-----------|-------|-----------|-------|
| F1 | **rsync transport** Mac→VPS, `-a --update` (deliver-only, **never `--delete`**) | per-brain | this brain's VPS node | Deliver-only so the VPS's cleaned/`cleared` captures aren't clobbered by the Mac's raw originals. Resurrection guard: Mac self-prune (age-based) and/or `--exclude-from=<retired-list>`. |
| F2 | **VPS retirement job** — delete a capture once `processed (watermark) AND LinOS-acked (manifest) ELSE 15-day fallback` | per-brain (decision co-resides with this brain's watermark on the VPS) | F1 + F3 | Deterministic timer or a reflection extension. Never deletes before both gates (or the fallback). |
| F3 | **LinOS consumer reflection + ack manifest** — reads only `linos-protostack` + `share_status: cleared` captures from each brain's inbox, integrates into LinOS's taxonomy, writes only to itself, publishes `{capture, content-hash}` acks to LinOS's own manifest | **joint** (built once on LinOS) | Phase C.5 (LinOS migrated to git-sync + deployed as `linos-*` agent node) | No brain writes/deletes inside another. This is the consumer half of the loop. |
| F4 | **`memory_dream.py`** — nightly procedural "dreaming" pass: sweep inbox + archive since a watermark, extract durable *how-I-work* patterns/processes/prompts (cheap Haiku), de-dupe against `playbook/`, append genuinely-new entries; strip project identifiers from confidential sources | per-brain | none (parallel future plan) | **Not built on BrunOS yet either** — mirror when BrunOS ships it. This is the PRD's "sleep consolidation" metaphor finally given its own mechanism, kept separate from reflection (opposite lifecycle: procedural knowledge accumulates monotonically, never compacted away). |
| F5 | **Phase D** federated-query MCP (`query(question)→answer`, per-pair ACL, answers tagged "external evidence"); **Phase E** bidirectional (LinOS→brain); **Phase F** VertikOS (stricter default-deny, per-employee ACLs, must be raised with Vertik founders first) | joint / future | Phase D after C.5 | Further out; list only as roadmap context. |

## Explicitly CUT — do not build on any brain

- **Episodic `memory_consolidate.py`** (instant feature-card compaction on a "done" marker
  + staleness). Judged low-value 2026-05-24; its one useful behaviour (draining inboxes) was
  folded into daily reflection (now contract C5). Do **not** resurrect it. If the brain
  already has a half-built `memory_consolidate.py`, flag it for removal, not completion.

## Per-brain difference reminders

When writing the PRD, translate BrunOS-specific names to this brain's equivalents — do not
copy them verbatim:

- systemd namespace: `lisaosbrain-*` (or this brain's own), **not** `brunoosbrain-*`.
- its own private vault git repo + its own paths + its own Slack app/token.
- on the **shared VPS**, never touch another user's namespace, services, home dir, or DB role.
