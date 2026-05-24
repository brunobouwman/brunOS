# BrunOS↔LinOS Federation — Producer Contract

The contract a personal brain must satisfy so the joint **LinOS** brain can safely consume
its work-scoped session knowledge. This is the *interface*, distilled from the BrunOS
reference implementation. Audit THIS brain against it; do not require byte-identical files.

**Mental model.** Working sessions in project repos are captured into a per-project inbox.
Daily reflection drains that inbox: it lifts durable personal knowledge into the personal
brain, maintains a per-project continuity doc, and **rewrites each capture with personal
asides stripped + `share_status: cleared`**. LinOS later reads only captures that are both
`default_export: linos-protostack` **and** `share_status: cleared`. The capture *is* the
shared artifact; `share_status: cleared` is the gate. There is no `_shared/` staging folder.

## Contract requirements

- [C1 — Capture file format + frontmatter schema](#c1)
- [C2 — `default_export` semantics + valid targets](#c2)
- [C3 — Capture hooks wired in project repos](#c3)
- [C4 — Inbox layout + slug canonicalization](#c4)
- [C5 — Reflection inbox stage (the core)](#c5)
- [C6 — `share_status: cleared` privacy gate](#c6)
- [C7 — Vault transport to where LinOS reads](#c7)
- [C8 — Uniform vault frontmatter](#c8)
- [C9 — Recursion guard + setting_sources discipline](#c9)
- [C10 — Confidentiality routing (Vertik/personal never crosses)](#c10)

The heavy, federation-specific ones are **C1, C2, C5, C6, C7, C10**. C8/C9 are infra-level
and usually already satisfied if the brain was built from the same foundation — verify lightly.

---

### C1 — Capture file format + frontmatter schema {#c1}

**What.** Each session capture is one markdown file at
`Memory/_inbox/sessions/<slug>/<YYYY-MM-DD>-<HHMMSS>-<sid8>.md`, frontmatter:
`type: inbox`, `created`, `updated` (RFC3339 `-03:00`), `project: <slug>`,
`default_export: <target>`, `session_id`, `source`, `tags` (block list: `inbox`, `<slug>`,
`session-capture`), `status: active`. Body = `## Memory flush (HH:MM)` + bullets.
Reference writer: `shared.write_inbox_capture`.

**Why.** LinOS keys off `project`, `default_export`, and (after reflection) `share_status`.
Missing/renamed fields break consumption.

**Check.** Pick a real capture and inspect its frontmatter:
`find Memory/_inbox/sessions -name '*.md' | head -1 | xargs sed -n '1,16p'`. Confirm
`shared.write_inbox_capture` emits exactly these fields.

**Rubric.** *Present*: real captures carry all fields. *Partial*: captures exist but a
required field is missing/renamed (e.g. no `default_export`). *Missing*: no inbox captures
or no writer.

---

### C2 — `default_export` semantics + valid targets {#c2}

**What.** `default_export ∈ {personal, linos-protostack, discard}` (the
`_VALID_EXPORT_TARGETS` set in `shared.py`); invalid values fall back to `personal`.
`linos-protostack` = LinOS may read once cleared; `personal` = never leaves this brain;
`discard` = audit-only.

**Why.** This is the per-company scoping LinOS filters on. A brain that tags everything
`personal` produces nothing LinOS can consume; one that tags Vertik work `linos-protostack`
would leak confidential content.

**Check.** `grep -n "_VALID_EXPORT_TARGETS" .claude/scripts/shared.py`; confirm Protostack
repos' captures are tagged `linos-protostack` and Vertik/private repos `personal`
(`grep -rl "default_export:" Memory/_inbox/sessions/<protostack-slug>/`).

**Rubric.** *Present*: valid set enforced + correct per-repo tagging. *Partial*: set exists
but a Protostack repo is mis-tagged `personal` (or vice versa). *Missing*: no export concept.

---

### C3 — Capture hooks wired in project repos {#c3}

**What.** Each worked-in project repo wires three hooks in its `.claude/settings.local.json`
(gitignored, host-specific absolute paths): **SessionStart** (`session-start-project.py
--project <slug> --context-file projects/<slug>.md --default-export <target>`),
**SessionEnd** + **PreCompact** (flush → `_inbox/sessions/<slug>/`). Codex repos use the
parallel `.codex/` wiring.

**Why.** No hooks → no captures → nothing to federate.

**Check.** In each project repo: `cat .claude/settings.local.json` and confirm the three
hook entries + the `--project`/`--default-export` flags. Cross-check the slug against C4.

**Rubric.** *Present*: all active project repos wired with correct flags. *Partial*: some
repos wired, others not, or missing PreCompact/SessionStart. *Missing*: no repos wired.

---

### C4 — Inbox layout + slug canonicalization {#c4}

**What.** One dir per project under `Memory/_inbox/sessions/`; `_`-prefixed dirs reserved
(e.g. `_archive`). A slug-alias map (`_SLUG_ALIASES` + `canonicalize_slug` in `shared.py`)
collapses path-derived/worktree/Codex variants onto one canonical slug so a repo never
splits across two folders. The Claude Code `--project` flag must point at the SAME canonical
slug as derivation produces.

**Why.** Split folders fragment a project's captures; LinOS-side per-company filtering
assumes one canonical slug per project.

**Check.** `ls Memory/_inbox/sessions/`; `grep -n "_SLUG_ALIASES\|canonicalize_slug"
.claude/scripts/shared.py`; confirm no duplicate folders for the same project.

**Rubric.** *Present*: canonical slugs, alias map covers this brain's repos. *Partial*:
aliases exist but a repo still split, or a repo's `--project` flag disagrees with derivation.
*Missing*: no canonicalization.

---

### C5 — Reflection inbox stage (the core) {#c5}

**What.** `memory_reflect.py` has an **inbox stage** (`_run_inbox_stage`): for each project
with captures newer than its watermark, **one Sonnet call per project** producing three
outputs — (1) durable personal items → `MEMORY.md` (cap-guarded), (2) continuity bullets →
`projects/<slug>.md` under a machine-managed `## Auto-consolidated continuity` section
(created if absent; hand-written header preserved; capped), (3) each capture rewritten
in place with personal asides stripped + `share_status: cleared`. Idempotent via a
**per-project watermark** in `.claude/data/state/inbox_reflection.json`
(`created > watermark` AND `share_status != cleared`). **Never deletes or moves** captures.
CLI: `--inbox-only`, `--skip-inbox`, `--project <slug>`, `--dry-run`.

**Why.** This is the engine that turns raw captures into the gated, work-scoped artifacts
LinOS reads. Without it, captures pile up unprocessed and never get a `share_status`.

**Check.** `grep -n "_run_inbox_stage\|inbox_reflection.json\|share_status\|Auto-consolidated
continuity" .claude/scripts/memory_reflect.py`; then a read-only
`<runner> memory_reflect.py --dry-run --inbox-only --project <slug>` and confirm
`git status Memory` shows no writes + the printed JSON has `personal`/`continuity`/
`would_clear`.

**Rubric.** *Present*: stage exists, 3 outputs, watermark, never-delete, dry-run clean.
*Partial*: only the daily-log stage exists, or inbox stage missing one output (e.g. no
continuity doc, or doesn't stamp `share_status`). *Missing*: `memory_reflect.py` has no
inbox stage.

---

### C6 — `share_status: cleared` privacy gate {#c6}

**What.** After stripping personal asides, reflection stamps each processed capture
`share_status: cleared` in frontmatter (inserted after `status:`, idempotent; `updated:`
restamped). Work/technical content is preserved verbatim; only personal-life asides are
removed.

**Why.** LinOS reads only `default_export: linos-protostack` AND `share_status: cleared`.
This flag IS the privacy boundary (replacing any `_shared/` staging copy).

**Check.** After a real (non-dry) inbox run on a copy, inspect a processed capture:
`grep -n "share_status" Memory/_inbox/sessions/<slug>/<file>.md` → `cleared`; eyeball that
personal asides are gone and work content intact.

**Rubric.** *Present*: processed captures stamped `cleared` with asides removed. *Partial*:
captures stamped but stripping is weak (personal content survives) or work content is lost.
*Missing*: no `share_status` ever written.

---

### C7 — Vault transport to where LinOS reads {#c7}

**What.** This brain's `cleared` captures must reach where LinOS reads them. Per the
federation model LinOS reads the **per-company inbox directly** on the shared VPS. Because
`Memory/_inbox/` is **gitignored + host-local**, transport is **rsync** (not vault git):
Mac→VPS `-a --update` (deliver-only, **never `--delete`**, so the VPS's cleaned/`cleared`
copies aren't clobbered by the Mac's raw originals). Reflection runs on the always-on VPS.

**Why.** Without a transport, captures cleared on the Mac never reach the host LinOS reads;
a `--delete` rsync would overwrite cleaned copies with raw ones.

**Check.** Is `_inbox/` gitignored (`grep -n "_inbox" Memory/.gitignore` or the vault repo's
ignore)? Is there an rsync unit/timer delivering the inbox to the VPS? Does reflection run on
the VPS (systemd `*-reflect` unit)? Much of this is **deferred** (see followups F1) — if so,
mark it as a tracked follow-up, not a today-gap.

**Rubric.** *Present*: `_inbox/` host-local + a deliver-only rsync path exists + reflection
runs VPS-side. *Partial*: some pieces (e.g. gitignore correct) but no transport yet.
*Missing/deferred*: no transport — route to followups, don't fail parity on it.

---

### C8 — Uniform vault frontmatter {#c8}

**What.** Every `Memory/` file carries `type / created / updated / tags (block list) /
status`. Reflection-created `projects/<slug>.md` must carry full frontmatter when created.

**Why.** Obsidian Properties filtering + the indexer assume it; malformed frontmatter breaks
both. Infra-level, but the continuity-doc writer must honor it.

**Check.** Grep the WHOLE vault, not a sample (a spot-check under-counts): `grep -rn "tags: \["
Memory/`. Matches inside README *body* examples don't count — only the `tags:` line inside a
file's leading `---…---` frontmatter block. Convert frontmatter-only, scoped to that block, and
**exclude `personal/finance.md`** (off-limits).

**Rubric.** *Present*: uniform. *Partial*: some files inline-array tags or miss a field.
*Missing*: no convention.

---

### C9 — Recursion guard + setting_sources discipline {#c9}

**What.** Every Agent SDK script sets `CLAUDE_INVOKED_BY=<purpose>` BEFORE importing
`claude_agent_sdk`; every `ClaudeAgentOptions(...)` passes `setting_sources` explicitly.

**Why.** Prevents SessionEnd-flush infinite loops and SDK-default drift. Infra-level —
likely satisfied if built from the same foundation; verify lightly.

**Check.** `grep -n "CLAUDE_INVOKED_BY\|setting_sources" .claude/scripts/memory_reflect.py`.

**Rubric.** *Present*: guard + explicit setting_sources. *Partial*: one present, not both.
*Missing*: neither.

---

### C10 — Confidentiality routing (Vertik/personal never crosses) {#c10}

**What.** Confidential work (Vertik, or anything `default_export: personal`) must NEVER reach
LinOS. The inbox stage clears captures regardless of export, but LinOS filters on
export+cleared, so a `personal` capture stays invisible to LinOS even once `cleared`. Codex
captures are hard-tagged `personal` by design.

**Why.** A single mis-tag leaks confidential client/employer content into a joint vault —
the highest-severity failure mode of the whole system.

**Check.** Confirm Vertik-equivalent repos and Codex captures are `personal`; confirm LinOS's
(future) read filter is `export==linos-protostack AND share_status==cleared`, never export
alone. `grep -rL "default_export: personal" Memory/_inbox/sessions/<vertik-slug>/` should
return nothing (i.e. every Vertik capture is `personal`).

**Rubric.** *Present*: confidential repos uniformly `personal`; filter is export+cleared.
*Partial*: mostly correct with a stray mis-tag. *Missing*: no deny-by-tag — **flag as
highest-priority gap**.
