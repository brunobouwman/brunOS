# Feature: BaaS Track C — Org / Onboarding Layer

The following plan should be complete, but it's important that you validate the codebase patterns
and task sanity before implementing. Pay special attention to the existing patterns in
`sanitize.py`, `shared.py`, and `memory_reflect.py` — all new code must follow them exactly.

## Feature Description

Implement the foundational org/access-policy layer for Brain-as-a-Service (BaaS). This is the
multi-tier access policy system, filing rules, excluded-entities privacy gate, and botmaster
onboarding kit that will govern how company brains are deployed, accessed, and operated. The
deliverables split into vault documents (policy artifacts) and code (excluded-entities scrub
integration + explicit read-scope registry).

ClickUp task: 86ca1z88k (protostack workspace). All 5 subtasks must ship.

## User Story

As a BrunOS/BaaS operator
I want explicit, code-enforced access policies, filing rules, privacy gates, and an onboarding playbook
So that company brains can be deployed with provable privacy separation and consistent content governance

## Problem Statement

The current federation model uses implicit naming conventions (e.g. `default_export: linos-protostack`)
as the read-scope filter. There are no: (a) formal access tiers for multi-org deployments, (b)
code-enforced read-scope validation, (c) excluded-entity scrubbing before captures are marked
cleared, (d) a filing decision tree, (e) an operator onboarding playbook.

## Solution Statement

1. Create `Memory/Brain/` vault directory with three policy docs (ACCESS_POLICY.md,
   botmaster-onboarding-kit.md, _brain-filing-rules.md).
2. Create `Memory/_excluded-people.md` (the instance for BrunOS's own brain) and a template at
   `Memory/Brain/_excluded-people-template.md`.
3. Add `CONSUMER_READ_SCOPES` + `validate_consumer_read()` to `shared.py` (explicit declared scopes).
4. Add `load_excluded_entities()` + `scrub_excluded_entities()` to `sanitize.py`.
5. Wire the excluded-entities scrub into `memory_reflect.py`'s `_strip_and_mark_capture()` — fail-closed.

## Feature Metadata

**Feature Type**: New Capability  
**Estimated Complexity**: Medium  
**Primary Systems Affected**: `shared.py`, `sanitize.py`, `memory_reflect.py`, vault `Memory/Brain/`  
**Dependencies**: None new — stdlib only; all vault files; no new packages  

---

## CONTEXT REFERENCES

### Relevant Codebase Files — MUST READ BEFORE IMPLEMENTING

- `.claude/scripts/shared.py` (lines 1-30) — `_VALID_EXPORT_TARGETS` set; add `CONSUMER_READ_SCOPES` here alongside it
- `.claude/scripts/shared.py` (lines 110-140) — `load_env()` pattern; stdlib-only constraint
- `.claude/scripts/sanitize.py` (full) — stdlib-only; all new functions must follow this pattern (no imports beyond `re`)
- `.claude/scripts/memory_reflect.py` lines 1-50 — module-level constants and imports pattern
- `.claude/scripts/memory_reflect.py` `_parse_capture()` (lines ~140-165) — how frontmatter is parsed; returns `(dict, body)` tuple
- `.claude/scripts/memory_reflect.py` `_strip_and_mark_capture()` (lines ~260-285) — WHERE to wire the excluded-entities scrub; the body is available here BEFORE `atomic_write`
- `.claude/scripts/memory_reflect.py` `_unprocessed_captures()` (lines ~170-200) — reads `share_status` and `created`; add `default_export` validation here using `validate_consumer_read()`
- `.claude/scripts/shared.py` `write_inbox_capture()` — `_VALID_EXPORT_TARGETS` is the current implicit guard; `CONSUMER_READ_SCOPES` supplements this

### New Files to Create

**Vault (in `/home/bruno/BrunOS/Memory/`):**
- `Memory/Brain/ACCESS_POLICY.md` — access tier policy (Full/Exec/Dept/None)
- `Memory/Brain/_brain-filing-rules.md` — first-match-wins filing decision tree
- `Memory/Brain/_excluded-people-template.md` — template for a company brain's excluded entities list
- `Memory/Brain/botmaster-onboarding-kit.md` — operator onboarding playbook
- `Memory/_excluded-people.md` — BrunOS's own excluded entities list (instance, not template)

**Code (in `/home/bruno/claude-second-brain/`):**
- No new files — all code changes go into existing files: `shared.py`, `sanitize.py`, `memory_reflect.py`

### Relevant Documentation

- `CLAUDE.md` § "Conventions" — vault frontmatter schema; every vault file MUST have full frontmatter
- `CLAUDE.md` § "Integrations (Phase 4)" — `_VALID_EXPORT_TARGETS` semantics
- `.claude/skills/federation-parity-audit/references/federation-contract.md` §C2, §C10 — the exact semantics of `default_export` and confidentiality routing that the `CONSUMER_READ_SCOPES` work is formalizing
- `.claude/skills/brunos-vault/SKILL.md` — vault layout and frontmatter requirements

### Patterns to Follow

**Frontmatter for vault files (MANDATORY):**
```yaml
---
type: reference    # or: project, system, etc.
created: 2026-05-30T23:XX:XX-03:00
updated: 2026-05-30T23:XX:XX-03:00
tags:
  - brain
  - baas
  - <specific-tag>
status: active
---
```
Use `shared._ts_brt()` pattern for timestamps; use current BRT time.

**stdlib-only in `sanitize.py` and `shared.py`:**
```python
# GOOD — no third-party imports
import re
# BAD — breaks hooks running on system python3
import yaml
```

**Fail-closed pattern (from `shared.py` / `memory_reflect.py`):**
```python
# When a safety check can't run, BLOCK — never silently pass
try:
    entities = load_excluded_entities(vault_path)
except Exception as e:
    _log(f"  excluded-entities load failed: {e}; refusing to clear capture (fail-closed)")
    return  # don't call _strip_and_mark_capture
```

**Atomic write pattern (from `shared.py`):**
```python
with file_lock(path):
    atomic_write(path, new_text)
```

**Constants declared at module level alongside related constants (from `shared.py`):**
```python
_VALID_EXPORT_TARGETS = {"personal", "linos-protostack", "discard"}

# NEW: explicit declared scopes — what each consumer may read (export-target allowlist)
CONSUMER_READ_SCOPES: dict[str, frozenset[str]] = {
    "linos": frozenset({"linos-protostack"}),
    # "vertikos": frozenset({"vertik"}),  # future company brain
}
```

**Function signature style (from `shared.py`):**
```python
def validate_consumer_read(capture_fm: dict, consumer: str) -> bool:
    """Return True iff capture's default_export is in the declared scope for `consumer`.

    Unknown consumers are denied (fail-closed). `cleared` flag is NOT checked here —
    that's LinOS's responsibility; this function only validates export scope.
    """
```

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation — shared.py additions (explicit read-scopes)

Add `CONSUMER_READ_SCOPES` dict and `validate_consumer_read()` function to `shared.py`.
These must be placed immediately after `_VALID_EXPORT_TARGETS` so they're collocated with
the related concept. No imports added — stdlib only.

### Phase 2: sanitize.py — excluded-entities functions

Add two functions to `sanitize.py`:
- `load_excluded_entities(vault_memory_path: Path) -> frozenset[str]` — reads `_excluded-people.md`, extracts entity names from lines starting with `- ` under the `## Excluded` section. Returns frozenset. Raises on IO error (let caller handle fail-closed).
- `scrub_excluded_entities(body: str, entities: frozenset[str]) -> tuple[str, int]` — replaces occurrences of each entity name with `[REDACTED-ENTITY]`. Returns (new_body, redaction_count). Case-insensitive word-boundary match. No external imports.

### Phase 3: memory_reflect.py — wire the gate

Two integration points in `memory_reflect.py`:
1. `_strip_and_mark_capture()` — after `clean_body = cleaned_body`, call `scrub_excluded_entities`. On failure (load error), return early without clearing (fail-closed).
2. `_unprocessed_captures()` — add optional `consumer` param (default `None`). When set, skip captures whose `default_export` is not in `CONSUMER_READ_SCOPES[consumer]`. Currently unused (LinOS isn't deployed yet), but the hook is wired so it's ready.

### Phase 4: Vault documents

Create `Memory/Brain/` directory and write five vault files:
1. `ACCESS_POLICY.md` — full YAML frontmatter + four-tier access policy table + rules
2. `_brain-filing-rules.md` — filing decision tree (first-match-wins)
3. `_excluded-people-template.md` — template with instructions + blank entity list
4. `botmaster-onboarding-kit.md` — three-phase onboarding playbook
5. `Memory/_excluded-people.md` — BrunOS instance (empty entity list, correct format)

---

## STEP-BY-STEP TASKS

### Task 1 — ADD `CONSUMER_READ_SCOPES` + `validate_consumer_read()` to `shared.py`

- **IMPLEMENT**: Insert after `_VALID_EXPORT_TARGETS = {...}` (line ~315 in shared.py — search for `_VALID_EXPORT_TARGETS`). Add:
  ```python
  # Explicit declared read-scopes for company-brain consumers.
  # Maps consumer slug → frozenset of allowed default_export values.
  # Unknown consumers are denied (fail-closed). LinOS reads only "linos-protostack".
  CONSUMER_READ_SCOPES: dict[str, frozenset[str]] = {
      "linos": frozenset({"linos-protostack"}),
      # Future: "vertikos": frozenset({"vertik"}),
  }


  def validate_consumer_read(capture_fm: dict, consumer: str) -> bool:
      """Return True iff capture's default_export is in the declared scope for consumer.

      Unknown consumers are denied (fail-closed). Does NOT check share_status —
      the consuming brain is responsible for that gate.
      """
      allowed = CONSUMER_READ_SCOPES.get(consumer)
      if allowed is None:
          return False  # unknown consumer → deny
      export = str(capture_fm.get("default_export") or "").strip()
      return export in allowed
  ```
- **PATTERN**: `_VALID_EXPORT_TARGETS` definition style (module-level dict), `shared.py`
- **IMPORTS**: None — stdlib only
- **GOTCHA**: `frozenset` ensures immutability; don't use a plain `set`
- **VALIDATE**: `uv run python -c "from .claude.scripts.shared import validate_consumer_read, CONSUMER_READ_SCOPES; print(CONSUMER_READ_SCOPES); print(validate_consumer_read({'default_export': 'linos-protostack'}, 'linos'))"`
  (Expected: `True`)

### Task 2 — ADD `load_excluded_entities()` + `scrub_excluded_entities()` to `sanitize.py`

- **IMPLEMENT**: Append to end of `sanitize.py` (after `wrap_external`):
  ```python
  # ---------------------------------------------------------------------------
  # Excluded-entities gate (Track C — Org layer)
  # ---------------------------------------------------------------------------

  _EXCLUDED_SECTION_RE = re.compile(r"^##\s+Excluded\b", re.MULTILINE | re.IGNORECASE)
  _EXCLUDED_ITEM_RE = re.compile(r"^-\s+(.+)$", re.MULTILINE)


  def load_excluded_entities(vault_memory_path) -> frozenset:
      """Load excluded entity names from Memory/_excluded-people.md.

      Reads lines starting with '- ' under the first '## Excluded' section.
      Raises OSError if the file cannot be read (caller must handle fail-closed).
      Returns an empty frozenset if the file exists but has no entries.
      `vault_memory_path` should be the Memory/ directory (a pathlib.Path).
      """
      from pathlib import Path
      path = Path(vault_memory_path) / "_excluded-people.md"
      text = path.read_text(encoding="utf-8")  # raises OSError on failure
      section_match = _EXCLUDED_SECTION_RE.search(text)
      if not section_match:
          return frozenset()
      section_text = text[section_match.end():]
      # Stop at the next heading
      next_heading = re.search(r"^##", section_text, re.MULTILINE)
      if next_heading:
          section_text = section_text[: next_heading.start()]
      names = {
          m.group(1).strip()
          for m in _EXCLUDED_ITEM_RE.finditer(section_text)
          if m.group(1).strip()
      }
      return frozenset(names)


  def scrub_excluded_entities(body: str, entities: frozenset) -> tuple:
      """Replace entity name occurrences in body with [REDACTED-ENTITY].

      Case-insensitive, whole-word match. Returns (scrubbed_body, redaction_count).
      If entities is empty, returns body unchanged with count 0.
      """
      if not entities:
          return body, 0
      count = 0
      result = body
      for name in entities:
          if not name:
              continue
          pattern = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
          new_result, n = pattern.subn("[REDACTED-ENTITY]", result)
          result = new_result
          count += n
      return result, count
  ```
- **PATTERN**: `_strip_injection_markers()` in `sanitize.py` — same regex-only approach; same stdlib-only constraint
- **IMPORTS**: `from pathlib import Path` inside the function (lazy import — sanitize.py must stay import-free at module level)
- **GOTCHA**: `sanitize.py` has ZERO top-level imports beyond `re`. Do not add `from pathlib import Path` at the top. Use a lazy import inside the function body only.
- **GOTCHA**: `re.escape(name)` is critical for names with special chars (dots, parentheses)
- **VALIDATE**: 
  ```bash
  uv run python -c "
  from pathlib import Path
  import sys; sys.path.insert(0, '.claude/scripts')
  from sanitize import scrub_excluded_entities
  body, n = scrub_excluded_entities('Alice Smith attended the meeting.', frozenset({'Alice Smith'}))
  print(body, n)
  "
  ```
  Expected: `[REDACTED-ENTITY] attended the meeting. 1`

### Task 3 — WIRE excluded-entities scrub into `memory_reflect.py` `_strip_and_mark_capture()`

- **IMPLEMENT**: In `memory_reflect.py`, add the following:
  1. Import `scrub_excluded_entities` and `load_excluded_entities` at the top of `_strip_and_mark_capture` function (lazy, inline — NOT at module level, because `sanitize` is already imported at module level, so just add to the existing import line: `from sanitize import wrap_external, scrub_excluded_entities, load_excluded_entities`).
  2. Inside `_strip_and_mark_capture()`, after the line `new_body = cleaned_body.rstrip() + "\n"` and BEFORE the `atomic_write` call, add:
  ```python
  # Excluded-entities gate — fail-closed: if we can't load the list, refuse to clear.
  from sanitize import load_excluded_entities, scrub_excluded_entities
  try:
      excluded = load_excluded_entities(vault_path() / "Memory")
  except FileNotFoundError:
      excluded = frozenset()  # no _excluded-people.md → no entities to scrub
  except Exception as e:
      _log(f"  excluded-entities load failed ({type(e).__name__}: {e}); skipping clear (fail-closed)")
      return
  if excluded:
      new_body, n = scrub_excluded_entities(new_body, excluded)
      if n > 0:
          _log(f"  {path.name}: scrubbed {n} excluded-entity mention(s)")
  ```
- **PATTERN**: `_strip_and_mark_capture()` body in `memory_reflect.py` — same guard-then-return pattern used for frontmatter check (`if not m: _log(...); return`)
- **IMPORTS**: `from sanitize import wrap_external` already exists at module level. Change to: `from sanitize import wrap_external, load_excluded_entities, scrub_excluded_entities`
- **GOTCHA**: `FileNotFoundError` is a subclass of `OSError`. Separate it: missing file = `frozenset()` (no list = no scrubbing needed); any other error = fail-closed (return without clearing).
- **GOTCHA**: The function signature is `_strip_and_mark_capture(path, fm, cleaned_body)`. `new_body` is computed inside the function from `cleaned_body`. Wire the scrub AFTER `new_body` is set, BEFORE `atomic_write`.
- **GOTCHA**: `vault_path()` is already imported from `shared` at module level in `memory_reflect.py`. Use it directly.
- **VALIDATE**: 
  ```bash
  uv run python .claude/scripts/memory_reflect.py --dry-run --inbox-only 2>&1 | head -20
  ```
  Should run without ImportError or syntax errors.

### Task 4 — CREATE `Memory/Brain/ACCESS_POLICY.md`

- **IMPLEMENT**: Write the file at `/home/bruno/BrunOS/Memory/Brain/ACCESS_POLICY.md` with full YAML frontmatter + content:

```markdown
---
type: reference
created: 2026-05-30T23:30:00-03:00
updated: 2026-05-30T23:30:00-03:00
tags:
  - brain
  - baas
  - access-policy
  - reference
status: active
---

# BaaS Access Policy — Multi-Tier Brain Access

Defines who can query, surface, and act on knowledge from a company brain. Applied per-deployment by the botmaster during onboarding. All tiers are cumulative downward (Full > Exec > Dept > None).

## Tiers

| Tier | Read scope | Digest surface | Action surface | Who |
|------|-----------|----------------|----------------|-----|
| **Full** | All company knowledge (all tags, all departments) | Full digest + gap analysis | May instruct agent actions | Bot owner / CTO / founder |
| **Exec** | Company-wide + cross-department context | Leadership digest | Advisory only (no agent actions) | C-suite, dept heads |
| **Dept** | Own department captures only | Department digest | None | Department leads, team leads |
| **None** | No direct brain access | None | None | Individual contributors (human) |

## Assignment rules

1. Default tier for all users: **None** (deny-by-default).
2. Tier elevation requires explicit declaration in `ACCESS_POLICY.md` per deployment.
3. **Full** tier is limited to ≤ 2 users per company brain (the botmaster + one backup).
4. **Exec** tier gives cross-department visibility — must be declared intentionally.
5. Bot user (the agent itself) always has Full read scope; its WRITE scope is separately governed by its SOUL.md.

## Department tag convention

Captures tagged `dept:<slug>` are scoped to that department. Examples:
- `dept:sales`, `dept:ops`, `dept:engineering`, `dept:cs`

Users with **Dept** tier can only read captures tagged with their own `dept:<slug>`.
Users with **Exec** or **Full** tier read across all `dept:*` tags.

## Enforcement model

Access policy is enforced at the **company brain's SOUL.md** level:
- SOUL.md declares permitted surfaces per-tier
- The agent checks caller context (Slack user, API key, etc.) against this file before surfacing knowledge
- No technical enforcement at this phase (declaration + honor system) — a future security gate will enforce at the API layer

## Per-deployment customization

Copy this template into the company brain's vault at `Memory/ACCESS_POLICY.md`. Declare the actual
users and their tiers in the `## Declared users` section below. Keep the schema stable — BaaS
infrastructure reads this file to configure the agent's surface restrictions.

## Declared users

_(Fill in per deployment)_

| User | Slack handle / email | Tier | Notes |
|------|----------------------|------|-------|
| (botmaster) | — | Full | Auto-assigned to whoever deploys |

## Changelog

- 2026-05-30 — Initial version, BrunOS BaaS Track C onboarding layer
```

- **PATTERN**: Vault frontmatter from `CLAUDE.md` + `sources_of_truth.md` — block-list `tags:`, RFC3339 `-03:00` timestamp
- **GOTCHA**: `Memory/Brain/` directory does not exist yet — create it with `mkdir -p`
- **VALIDATE**: 
  ```bash
  ls /home/bruno/BrunOS/Memory/Brain/
  head -10 /home/bruno/BrunOS/Memory/Brain/ACCESS_POLICY.md
  ```

### Task 5 — CREATE `Memory/Brain/_brain-filing-rules.md`

- **IMPLEMENT**: Write at `/home/bruno/BrunOS/Memory/Brain/_brain-filing-rules.md`:

```markdown
---
type: reference
created: 2026-05-30T23:30:00-03:00
updated: 2026-05-30T23:30:00-03:00
tags:
  - brain
  - baas
  - filing-rules
  - reference
status: active
---

# Brain Filing Rules — First-Match-Wins Decision Tree

When new content arrives (from a session capture, a Slack digest, or manual input), apply
these rules top-to-bottom. Stop at the first match. This is the filing decision tree for
a company brain built on the BaaS platform.

## Decision Tree

```
Is this a task with a status that changes (todo → doing → done)?
  YES → ClickUp (execution layer) — do NOT put in brain
  NO  → continue ↓

Is this a secret / credential / PII?
  YES → STOP. Never file in brain. Use your password manager / secrets vault.
  NO  → continue ↓

Does it describe a person who appears in _excluded-people.md?
  YES → STOP. Do not file — excluded entity.
  NO  → continue ↓

Is this a decision (with a "why" and reversal conditions)?
  YES → projects/<project>.md under "## Decisions" OR MEMORY.md if org-wide
  NO  → continue ↓

Is this a lesson (something learned the hard way)?
  YES → MEMORY.md "## Lessons" section
  NO  → continue ↓

Is this a client/customer-facing fact (pricing, SLA, contact, preference)?
  YES → clients/<client-slug>.md
  NO  → continue ↓

Is this a project-specific fact or working context?
  YES → projects/<project-slug>.md body
  NO  → continue ↓

Is this a department-wide policy or process?
  YES → team/<dept-slug>.md OR Memory/ACCESS_POLICY.md if it's an access rule
  NO  → continue ↓

Is this a one-off note, meeting output, or ephemeral context?
  YES → daily/<YYYY-MM-DD>.md (daily log) — reflection promotes if durable
  NO  → continue ↓

None of the above matched → file in research/<topic>.md or leave in daily log
```

## Filing locations

| Location | Contains |
|----------|----------|
| `MEMORY.md` | Durable cross-project facts, decisions, lessons — promoted by reflection |
| `projects/<slug>.md` | Per-project context, decisions, architecture, status |
| `clients/<slug>.md` | Client/customer facts, preferences, SLAs, contacts |
| `team/<dept>.md` | Department-wide policies, processes, role definitions |
| `daily/<YYYY-MM-DD>.md` | Ephemeral captures, meeting notes, one-off context |
| `research/<topic>.md` | Deep-dive research, not tied to a specific project |
| `goals/<slug>.md` | OKRs, strategic goals, review drafts |
| ClickUp | Tasks, status, due dates, assignees — execution layer |

## Anti-patterns to avoid

- **Do NOT** duplicate a task in both ClickUp and the brain — if it has a status, it's a ClickUp task
- **Do NOT** file credentials or secrets in any brain file — ever
- **Do NOT** create a new top-level folder without updating this file
- **Do NOT** let the `daily/` log accumulate undrained — reflection must run daily

## Changelog

- 2026-05-30 — Initial version, BrunOS BaaS Track C onboarding layer
```

- **VALIDATE**: `head -5 /home/bruno/BrunOS/Memory/Brain/_brain-filing-rules.md`

### Task 6 — CREATE `Memory/Brain/_excluded-people-template.md`

- **IMPLEMENT**: Write at `/home/bruno/BrunOS/Memory/Brain/_excluded-people-template.md`:

```markdown
---
type: reference
created: 2026-05-30T23:30:00-03:00
updated: 2026-05-30T23:30:00-03:00
tags:
  - brain
  - baas
  - privacy
  - excluded-entities
  - reference
status: active
---

# Excluded People / Entities — Template

This template is for company brain deployments. Copy it to `Memory/_excluded-people.md` in
the company brain vault and fill in the entities that should never appear in any output
surfaced by the brain (digest, query response, PR review, etc.).

**How it works:** The BrunOS reflection inbox stage reads `Memory/_excluded-people.md` before
marking any capture as `share_status: cleared`. Any mention of an excluded entity in the
capture body is replaced with `[REDACTED-ENTITY]`. This runs fail-closed: if the file exists
but can't be parsed, the capture is NOT cleared (it stays unprocessed and the error is logged).

## When to add an entity

Add a person, org, or entity when:
- They appear in captured work content but must NEVER appear in shared/surfaced brain outputs
- They are a competitor or sensitive partner whose mention would be problematic if leaked
- They are a private individual whose name should not appear in any AI-processed content

## Format

Each entry under `## Excluded` is a single line starting with `- `. The name is matched
case-insensitively, whole-word. Include the most common form of the name (e.g. "John Smith"
not "John E. Smith" unless the middle initial is how they appear in captures).

## Excluded

_(Add entries below — one per line)_

<!-- Example entries (delete before deploying):
- Competitor Corp
- Jane Doe
- Some Sensitive Partner Ltd
-->

## Changelog

- (fill in date) — Initial excluded-entities list for <company-brain-name>
```

- **VALIDATE**: `head -5 /home/bruno/BrunOS/Memory/Brain/_excluded-people-template.md`

### Task 7 — CREATE `Memory/_excluded-people.md` (BrunOS instance)

- **IMPLEMENT**: Write at `/home/bruno/BrunOS/Memory/_excluded-people.md` — this is BrunOS's own instance (not the template):

```markdown
---
type: reference
created: 2026-05-30T23:30:00-03:00
updated: 2026-05-30T23:30:00-03:00
tags:
  - brain
  - baas
  - privacy
  - excluded-entities
  - reference
status: active
---

# Excluded People / Entities — BrunOS Instance

People and entities that must never appear in any brain output surfaced to company consumers.
This file is read by `memory_reflect.py` before marking inbox captures as `share_status: cleared`.

For the template and instructions, see `Memory/Brain/_excluded-people-template.md`.

## Excluded

_(Empty — add entries when needed)_

## Changelog

- 2026-05-30 — Initial BrunOS instance, empty (no excluded entities at launch)
```

- **VALIDATE**: 
  ```bash
  uv run python -c "
  import sys; sys.path.insert(0, '.claude/scripts')
  from sanitize import load_excluded_entities
  from pathlib import Path
  e = load_excluded_entities(Path('/home/bruno/BrunOS/Memory'))
  print('Loaded:', e)
  "
  ```
  Expected: `Loaded: frozenset()`

### Task 8 — CREATE `Memory/Brain/botmaster-onboarding-kit.md`

- **IMPLEMENT**: Write at `/home/bruno/BrunOS/Memory/Brain/botmaster-onboarding-kit.md`:

```markdown
---
type: reference
created: 2026-05-30T23:30:00-03:00
updated: 2026-05-30T23:30:00-03:00
tags:
  - brain
  - baas
  - onboarding
  - reference
status: active
---

# Botmaster Onboarding Kit

Playbook for deploying a new company brain on the BaaS platform. Three phases: **Pre-populate**,
**Wow flows**, **Graduate**. Each phase has a checklist and estimated time.

---

## Phase 1 — Pre-populate (Day 1, ~2h)

Goal: seed the brain with enough context to deliver immediate value on Day 1.

### Checklist

- [ ] **Set up vault structure** — run `deploy/bin/seed-company-on-host.sh <company-slug>` (creates Memory/ dirs, installs concat-both merge driver)
- [ ] **Fill ACCESS_POLICY.md** — copy from `Memory/Brain/_excluded-people-template.md`; declare ≤2 Full-tier users, Exec and Dept users
- [ ] **Fill _excluded-people.md** — list any entities that must never appear in surfaced outputs; review with the client
- [ ] **Write SOUL.md** — define the company brain's identity: what it is, what it's not, tone, action surface (draft-only vs autonomous), confidentiality scope
- [ ] **Seed initial projects/** — one file per active project (copy the frontmatter template; at minimum: project name, what it is, who's on it, current status)
- [ ] **Seed initial clients/** — one file per active client/customer (name, key contacts, current status, known preferences)
- [ ] **Seed initial team/** — one file per department (what the dept does, who leads it, key processes)
- [ ] **Wire capture hooks** in the company's main project repos — `SessionStart` + `SessionEnd` + `PreCompact` hooks pointing to this brain's `_inbox/sessions/<slug>/`
- [ ] **Configure default_export tags** — Protostack → `linos-protostack`; confidential → `personal`; discard → `discard`
- [ ] **Set up vault git sync** — run `deploy/bin/install-merge-driver.sh` on both Mac + VPS
- [ ] **Test a capture** — make a small edit in a wired repo, end the session, confirm capture appears in `Memory/_inbox/sessions/<slug>/`

### Deliverable

A brain that can answer "what are our active projects?" and "who is [client]?" from its seeded context.

---

## Phase 2 — Wow Flows (Week 1, ~1h demo)

Goal: demonstrate 2-3 high-value brain capabilities to build trust and drive adoption.

### Wow Flow A — Morning Briefing

Trigger: `@brain what's on the plate today?`

Expected output:
- ClickUp tasks due today + overdue (pulled via integration)
- Active projects with recent captures (pulled from sessions)
- Any flagged items from overnight Slack/email
- 1-2 proactive suggestions based on context

Setup required: ClickUp integration configured, Slack bot deployed, heartbeat running.

**Demo script:**
1. Show an empty morning — "Here's what the brain sees"
2. Create a ClickUp task + send a Slack message
3. Show next morning briefing includes both

### Wow Flow B — Knowledge Query

Trigger: `@brain what did we decide about [topic]?`

Expected output:
- Relevant past decisions pulled from projects/*.md and MEMORY.md
- Source file + date of decision
- Reversal conditions if any

Setup required: memory_search indexed, at least a few days of captures processed.

**Demo script:**
1. Reference a decision made in a previous session (must have been captured)
2. Ask the brain about it cold — "What did we decide about the API auth approach?"
3. Show the brain surfacing the exact decision + date + source

### Wow Flow C — Weekly Gap Analysis

Trigger: `@brain what are we missing or behind on?`

Expected output:
- Overdue ClickUp tasks + stale projects (no captures in 7+ days)
- Open questions / unresolved items from recent sessions
- Habit pillars or commitments not actioned this week

Setup required: weekly review skill deployed, heartbeat running 5+ days.

**Demo script:**
1. Leave a task unfinished for several days
2. Run the weekly gap analysis Friday afternoon
3. Show the brain identifies the gap + suggests the next action

---

## Phase 3 — Graduate (End of Month 1)

Goal: client is self-sufficient; transition from CS-intensive to retainer.

### Graduation checklist

- [ ] Client can configure ACCESS_POLICY.md without help
- [ ] Client can add/update excluded entities
- [ ] Client has filed at least 10 sessions worth of captures (organic growth)
- [ ] Client has completed all three Wow Flows at least once
- [ ] Weekly review is running autonomously (scheduled, no manual trigger)
- [ ] Client understands the ClickUp ↔ brain division (execution vs thinking)
- [ ] At least one Exec-tier user is actively using morning briefings
- [ ] Botmaster has handed off the Full-tier backup seat to a client-side admin

### Retainer scope (post-graduation)

After graduation, BaaS support transitions to retainer:
- Monthly brain health check (gap analysis, stale projects, MEMORY.md cap)
- Quarterly SOUL.md review (is the brain still doing the right things?)
- On-demand access-policy updates (new employees, role changes)
- Incident response for privacy issues (unexpected entity mentions, routing errors)

---

## Pricing reference

See `Memory/Brain/baas_gtm_pricing.md` (when created) for current pricing tiers.
Short version: implementation fee (one-time) + monthly retainer (per seat × tier).

## Changelog

- 2026-05-30 — Initial version, BrunOS BaaS Track C onboarding layer
```

- **VALIDATE**: `wc -l /home/bruno/BrunOS/Memory/Brain/botmaster-onboarding-kit.md`

### Task 9 — VALIDATE all code changes together

- **VALIDATE**:
  ```bash
  # 1. Syntax check all modified Python files
  uv run python -m py_compile .claude/scripts/shared.py && echo "shared.py OK"
  uv run python -m py_compile .claude/scripts/sanitize.py && echo "sanitize.py OK"
  uv run python -m py_compile .claude/scripts/memory_reflect.py && echo "memory_reflect.py OK"
  
  # 2. Import check
  uv run python -c "from .claude.scripts.shared import validate_consumer_read, CONSUMER_READ_SCOPES; print('shared OK')"
  uv run python -c "
  import sys; sys.path.insert(0, '.claude/scripts')
  from sanitize import load_excluded_entities, scrub_excluded_entities
  print('sanitize OK')
  "
  
  # 3. Integration check — reflect dry-run (no vault writes)
  uv run python .claude/scripts/memory_reflect.py --dry-run --inbox-only 2>&1
  
  # 4. Vault files exist
  ls /home/bruno/BrunOS/Memory/Brain/
  ls /home/bruno/BrunOS/Memory/_excluded-people.md
  ```

---

## TESTING STRATEGY

### Unit Tests

No test framework is in pyproject.toml — project uses manual validation via `--dry-run` flags.
Inline validation commands above serve as unit tests.

Key validation assertions:
- `validate_consumer_read({"default_export": "linos-protostack"}, "linos")` → `True`
- `validate_consumer_read({"default_export": "personal"}, "linos")` → `False`
- `validate_consumer_read({"default_export": "linos-protostack"}, "unknown-brain")` → `False`
- `load_excluded_entities(Path("/home/bruno/BrunOS/Memory"))` → `frozenset()` (empty list)
- `scrub_excluded_entities("Alice attended.", frozenset({"Alice"}))` → `("[REDACTED-ENTITY] attended.", 1)`
- `scrub_excluded_entities("alice here.", frozenset({"Alice"}))` → `("[REDACTED-ENTITY] here.", 1)` (case-insensitive)
- `scrub_excluded_entities("Aliceboard", frozenset({"Alice"}))` → `("Aliceboard", 0)` (word boundary respected)

### Edge Cases

1. `_excluded-people.md` missing — `FileNotFoundError` → `frozenset()` (empty, no scrubbing — fine)
2. `_excluded-people.md` exists but malformed (no `## Excluded` section) → `frozenset()` (empty — safe)
3. `_excluded-people.md` exists but unreadable (permissions) → `OSError` raised → caller returns early (fail-closed)
4. Entity name with regex special chars (e.g. "Acme.Corp") — `re.escape()` handles it
5. Empty entities set — `scrub_excluded_entities` returns body unchanged, count 0
6. `validate_consumer_read` with None `default_export` — `str(None).strip()` = `"None"` → not in allowed → False (correct)

---

## VALIDATION COMMANDS

### Level 1: Syntax
```bash
uv run python -m py_compile .claude/scripts/shared.py
uv run python -m py_compile .claude/scripts/sanitize.py
uv run python -m py_compile .claude/scripts/memory_reflect.py
```

### Level 2: Function-level
```bash
uv run python -c "
import sys; sys.path.insert(0, '.claude/scripts')
from shared import validate_consumer_read, CONSUMER_READ_SCOPES
assert validate_consumer_read({'default_export': 'linos-protostack'}, 'linos') == True
assert validate_consumer_read({'default_export': 'personal'}, 'linos') == False
assert validate_consumer_read({'default_export': 'linos-protostack'}, 'bogus') == False
print('validate_consumer_read: all assertions passed')
"

uv run python -c "
import sys; sys.path.insert(0, '.claude/scripts')
from sanitize import load_excluded_entities, scrub_excluded_entities
from pathlib import Path

# scrub test
body, n = scrub_excluded_entities('Alice attended.', frozenset({'Alice'}))
assert body == '[REDACTED-ENTITY] attended.' and n == 1, f'Got: {body!r}, {n}'
body2, n2 = scrub_excluded_entities('Aliceboard', frozenset({'Alice'}))
assert n2 == 0, f'Expected 0, got {n2}'
body3, n3 = scrub_excluded_entities('alice here', frozenset({'Alice'}))
assert n3 == 1, f'Expected 1, got {n3}'

# load test (empty list)
e = load_excluded_entities(Path('/home/bruno/BrunOS/Memory'))
assert e == frozenset(), f'Expected empty frozenset, got {e}'

print('sanitize: all assertions passed')
"
```

### Level 3: Integration (reflect dry-run)
```bash
uv run python .claude/scripts/memory_reflect.py --dry-run --inbox-only 2>&1
# Expected: no ImportError, no syntax errors, normal "no project inboxes" or dry-run output
```

### Level 4: Vault files
```bash
ls -la /home/bruno/BrunOS/Memory/Brain/
grep "^type:" /home/bruno/BrunOS/Memory/Brain/ACCESS_POLICY.md
grep "^type:" /home/bruno/BrunOS/Memory/Brain/_brain-filing-rules.md
grep "^type:" /home/bruno/BrunOS/Memory/Brain/botmaster-onboarding-kit.md
grep "^type:" /home/bruno/BrunOS/Memory/Brain/_excluded-people-template.md
grep "^type:" /home/bruno/BrunOS/Memory/_excluded-people.md
```

---

## ACCEPTANCE CRITERIA

- [ ] `CONSUMER_READ_SCOPES` dict exists in `shared.py` alongside `_VALID_EXPORT_TARGETS`
- [ ] `validate_consumer_read()` function in `shared.py` passes all 3 assertions above
- [ ] `load_excluded_entities()` in `sanitize.py` parses `## Excluded` section correctly
- [ ] `scrub_excluded_entities()` in `sanitize.py` does case-insensitive whole-word replacement
- [ ] `_strip_and_mark_capture()` in `memory_reflect.py` calls scrub before `atomic_write`
- [ ] Fail-closed: non-FileNotFoundError on load causes `_strip_and_mark_capture` to return without clearing
- [ ] `Memory/Brain/` directory exists with 4 files: ACCESS_POLICY.md, _brain-filing-rules.md, botmaster-onboarding-kit.md, _excluded-people-template.md
- [ ] `Memory/_excluded-people.md` exists with correct frontmatter and empty entity list
- [ ] All vault files have full YAML frontmatter (type, created, updated, tags block-list, status)
- [ ] All Python syntax checks pass (`py_compile`)
- [ ] `memory_reflect.py --dry-run --inbox-only` runs without error

---

## COMPLETION CHECKLIST

- [ ] All 9 tasks completed in order
- [ ] Each task's VALIDATE command executed and passed
- [ ] Level 1-4 validation commands all pass
- [ ] All acceptance criteria checked
- [ ] No linting/syntax errors

---

## NOTES

### Why `shared.py` and not a new `access_policy.py`

`shared.py` is stdlib-only and imported by hooks running under system python3 (no .venv). Adding
`CONSUMER_READ_SCOPES` there keeps the read-scope registry collocated with `_VALID_EXPORT_TARGETS`
(the related concept) and accessible from hooks without `.venv`. A new module would require a
lazy import chain.

### Why `sanitize.py` for excluded-entities functions

`sanitize.py` is the existing trust-boundary module. The excluded-entities gate is a privacy
boundary — semantically the right place. Its stdlib-only constraint is maintained by using a lazy
`from pathlib import Path` import inside the function body (Path is stdlib, so this is fine).

### Why FileNotFoundError → empty frozenset (not fail-closed)

If `_excluded-people.md` doesn't exist, there are no excluded entities declared. An empty frozenset
is correct behavior (no scrubbing needed). The file's ABSENCE is not a configuration error. A file
that EXISTS but can't be READ is a configuration error → fail-closed.

### CONSUMER_READ_SCOPES is BrunOS-side only

LinOS (the consuming brain) will need to call `validate_consumer_read()` before processing any
capture it reads. This plan adds the registry and function to BrunOS; LinOS integration is Track A
(Phase C — company-brain consumer loop, task 86ca1z83j). This plan prepares the contract; Track A
enforces it on the consumer side.

### Vault Brain/ directory vs Memory/ root

`_excluded-people.md` lives at `Memory/` root (one level up from `Brain/`) because:
- It's an instance (BrunOS's own excluded list), not a template
- It's read by `memory_reflect.py` which knows the vault Memory/ path
- Easier to find for operators

Templates and policy docs go under `Memory/Brain/` as they are BaaS product artifacts.

### No new Python packages

All changes use existing `re` + `pathlib` + the already-installed libraries. `pyproject.toml`
does NOT need updates.
