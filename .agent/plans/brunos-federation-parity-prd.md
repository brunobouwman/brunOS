# Federation Parity — PRD (BrunOS)

_Draft for review — refine before implementing. Build one task at a time; dry-run-validate each._

BrunOS is producer-complete on logic (C1–C6, C9, C10). Only two items need action: one
hygiene nit (C8) and the binding transport gap (C7). Everything else is deferred follow-up.

## Parity tasks (ordered, atomic, independently testable)

### Task 1 — Normalize project-file frontmatter tags (closes C8)
- **Goal:** every `projects/*.md` uses block-list `tags:`, not inline arrays.
- **Clone-vs-adapt:** n/a — local edit.
- **Files:** `BrunOS/Memory/projects/vertik.md` (line 5 `tags: [vertik, contract]` → block list); grep the rest: `grep -rn "tags: \[" BrunOS/Memory/`.
- **Validation:** `grep -rn "tags: \[" BrunOS/Memory/` returns nothing; `uv run python .claude/scripts/memory_index.py --paths BrunOS/Memory/projects/vertik.md --dry-run` exits 0.
- **Acceptance:** no inline-array tags remain in the vault.

### Task 2 — Resolve the inbox-stage transport gap (closes C7)
- **Goal:** captures reach the host where the inbox stage runs, so the stage actually processes them.
- **Decision (pick one):**
  - **(a) Interim — run the inbox stage on the Mac.** Captures already land on the Mac; the daily-log stage can stay VPS-side, but invoke `memory_reflect.py --inbox-only` on the Mac (launchd) until transport exists. Zero new transport code; cost is the Mac must be up.
  - **(b) Durable — build F1 rsync transport** (see Deferred). Then the VPS inbox stage works unattended.
- **Files:** (a) a Mac launchd plist for `--inbox-only`; or (b) the F1 rsync unit + the VPS retirement guard.
- **Validation:** on whichever host runs it, `memory_reflect.py --dry-run --inbox-only` finds the real captures (non-empty `would_clear`); `git status BrunOS/Memory` clean after dry-run.
- **Acceptance:** a real inbox-stage run drains the current 9 colinas + 39 vertik captures (stamps `cleared`, builds continuity), and re-run is a no-op.

## Deferred follow-ups (mirror BrunOS roadmap — NOT parity gaps)

- **F1 — rsync transport** Mac→VPS, `-a --update` (deliver-only, **never `--delete`**). _per-brain._ Resurrection guard: Mac self-prune and/or `--exclude-from`. (This is the durable fix for Task 2.)
- **F2 — VPS retirement job:** delete a capture once `processed (watermark) AND LinOS-acked ELSE 15-day fallback`. _per-brain_, blocked on F1 + F3.
- **F3 — LinOS consumer reflection + ack manifest:** reads only `linos-protostack` + `share_status: cleared` captures, integrates into LinOS, writes only to itself, publishes acks. **joint (built once on LinOS)**, blocked on Phase C.5 (LinOS-as-agent).
- **F4 — `memory_dream.py`** procedural dreaming→playbook pass. _per-brain_, not built on BrunOS yet either — build here first, then mirror to LisaOS.
- **F5 — Phase D** federated-query MCP; **Phase E** bidirectional; **Phase F** VertikOS. Roadmap context only.
- **CUT — do NOT build:** episodic `memory_consolidate.py` (folded into reflection C5; stays cut).

## Sequencing note

Producer parity is essentially done (Tasks 1–2 close the remainder). The end-to-end loop then
waits on the LinOS consumer (F3), blocked on LinOS-as-agent (Phase C.5) — so after Task 2, pause
federation build until LinOS can read the cleared captures.
