# Handoff: inbox clearing under-clears (watermark skips non-echoed captures)

**Status:** flagged, NOT fixed. Owned by whoever works the inbox/clearing path
(the BrunOS→LinOS transport session is closest). Filed 2026-06-02.

## Symptom

Captures get reflected (their content reaches `projects/<slug>.md` continuity +
`MEMORY.md`) but never receive `share_status: cleared`. On the live VPS this
shows as a gap between total captures and cleared captures (~119 cleared of ~158
total observed 2026-06-02). Because the transport gate is `default_export ==
linos-protostack AND share_status == cleared`, an under-cleared capture is also
never transported.

## Root cause

`.claude/scripts/memory_reflect.py`, inbox stage:

- `_unprocessed_captures(slug, watermark)` selects captures where
  `created > watermark AND share_status != cleared`.
- `_process_inbox_batch()` clears ONLY the captures the LLM echoes back in
  `cleaned_captures` with a filename that matches `by_name`
  (`matched_cleaned = [c for c in cleaned if c["capture"] in by_name]`), then
  advances the watermark to `max_created` over **all** captures in the batch.

So if the LLM omits a capture from `cleaned_captures` (or returns a filename that
doesn't exactly match), that capture is:
1. never `_strip_and_mark_capture`'d → stays `share_status != cleared`, AND
2. left below the advanced watermark → `created <= watermark` excludes it on every
   future run.

→ permanently stuck uncleared. The `INBOX_SYSTEM_PROMPT` instructs "for EVERY
input capture, return an entry", but that's a soft contract the watermark logic
trusts absolutely.

## Fix options (pick per transport-session design)

1. **Don't advance the watermark past uncleared captures.** Set the per-slug
   watermark to the newest `created` among captures that were ACTUALLY cleared
   this run (or the min of the un-cleared set minus epsilon). Uncleared ones stay
   eligible and retry next run. Risk: a capture the LLM *persistently* refuses to
   echo loops forever — bound with a per-capture attempt counter that, after N
   tries, force-clears via deterministic scrub only (accepting that the LLM's
   personal-aside removal didn't run for that one) or moves it to a
   `share_status: quarantined` that the new fail-closed guard already refuses to
   share.
2. **Decouple clearing from the echo.** After the batch, clear every input
   capture: use the LLM's cleaned body where echoed, else fall back to the
   capture's existing body run through `scrub_excluded_entities` + `scrub_secrets`
   (Track B/C deterministic scrubs). Caveat: the deterministic scrubs do NOT
   remove general personal asides (mood/health/family) the way the LLM body does,
   so this slightly weakens privacy for non-echoed captures — acceptable only if
   the prompt reliably echoes and this is a rare backstop.

Option 1 is safer for privacy; option 2 is simpler and guarantees progress.

## Related (already shipped 2026-06-02)

- Fail-closed `share_status` guard in `_strip_and_mark_capture`
  (`_STRIP_OPEN_STATUSES = {None, "", "active"}`) — unknown statuses refuse to
  clear. A force-clear/quarantine fix above should set a status this guard
  recognizes.
- `write_inbox_capture` now canonicalizes the slug (fix/inbox-slug-canonicalize),
  so this is no longer compounded by repos splitting across folders.
