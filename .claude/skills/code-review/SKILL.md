---
name: code-review
description: >-
  Review a code change in a PROJECT repo — a worktree diff, a branch, or a PR — for
  correctness, security, the project's own standards, test coverage, and simplicity, and
  return an advisory review with severity-tagged findings, citations, suggested fixes, and
  an explicit uncertainty section. Use when asked to "review this branch/PR/diff", "is this
  ready to merge", "code review before the PR", or as the reviewer gate inside the dev-task
  pipeline's verify stage. Runs a deterministic pre-PR safety gate first (conflict markers,
  staged secrets, stray debug, off-main, validations-ran), then the judgment review. Targets
  project repos only — HARD-REFUSES the BrunOS code repo and the vault. Advisory + draft-only:
  it never auto-posts to GitHub; a human or the pipeline decides to post.
---

# Code Review (individual dev pipeline)

Review a change in a **project repo** before it becomes a PR — the reviewer gate for the
`dev-task` pipeline, and a standalone "review my branch" tool. It is the individual-brain
sibling of `company-judge`: same review *discipline*, different *corpus* (the project's own
standards, not a company `STANDARDS.md`) and different *role* (individual, not company).

## Two hard rules

1. **Project repos only.** HARD-REFUSE if the target is the **BrunOS code repo** (the
   read-only `code-sync` consumer on the VPS) or the **vault** — a stray branch/commit there
   silently breaks `git pull --ff-only`. Same guard as `dev-task`. Review worktrees/PRs of
   project repos (vertik-lab-agent, colinas, chat-ui, …) only.
2. **Advisory + draft-only.** Findings are recommendations; the author may push back with
   reasoning. **Never auto-post** to GitHub/Slack — posting a review comment is draft-only +
   ask-first (SOUL/CLAUDE.md). Reviews use the `query.py github` integration (FGPAT), not the
   `gh` CLI (absent on the VPS), and only to *draft*.

## Inputs

- **The change** — a worktree path + base (e.g. `HEAD` vs `main`), a branch name, or a PR
  reference. Work from the **diff + the project's standards docs** — crafted context, not a
  whole session history.
- Optional: the plan/requirements the change implements; build/lint/test output.

## Method (reused, not re-derived)

The severity model, evidence-before-claims rule, and output skeleton are shared with the
Judge — read
[`../company-judge/references/review-methodology.md`](../company-judge/references/review-methodology.md):
**Critical / Important / Minor**; *if a "tests pass" claim has no fresh output it is
unverified*; return **verdict + findings + strengths + uncertainty**. The deterministic
pre-PR gate is the dev-task floor —
[`../dev-task/references/execution-pitfalls.md`](../dev-task/references/execution-pitfalls.md).

## Workflow

### Phase 1 — Deterministic pre-PR gate (mechanical, runs FIRST)

Run the `execution-pitfalls.md` one-liners against the worktree/diff — these are pass/fail,
no judgment:

- **Off main** — branch ≠ `main` (and the repo is NOT the BrunOS code repo / vault → else ABORT).
- **No conflict markers** — `<<<<<<<` / `=======` / `>>>>>>>`.
- **No staged secrets / `.env`** — no `.env`, `*.pem`, `*.key`, `secrets/` in the staged set.
- **No stray debug** — `console.log` / `debugger` / `breakpoint(` / `import pdb` / `binding.pry`.
- **Validations ran** — the plan's build/lint/test commands actually executed green (don't
  trust "looks done" — evidence before claims).

Any gate failure is a **Critical** finding and blocks until fixed.

### Phase 2 — Load the project's standards

Read the **project's own** `CLAUDE.md` / `README.md` / `docs/` to learn its conventions,
architecture, and patterns. The review cites *these*, not a company corpus. (No standards
doc? Fall back to general correctness/security/simplicity and note the absence.)

### Phase 3 — Judgment review (dimensions)

Review the diff across these dimensions; tag each finding with severity + a citation
(project standard, or "general best practice" when no local standard governs) + a suggested fix:

1. **Correctness / bugs** — logic errors, edge cases, error handling, off-by-ones, race
   conditions, broken contracts. *Critical if it breaks behavior.*
2. **Security** — injection, secret handling, auth/scope, **fail-open on unknown input**,
   unsafe deserialization. *Critical.*
3. **Project standards** — does it follow the repo's CLAUDE.md/conventions, architecture,
   naming? *Important.*
4. **Test coverage** — are new paths tested? Red-green verified for a fix? *Important if a
   risky change is untested.*
5. **Simplicity / readability** — dead code, needless complexity, unclear intent. *Minor.*

### Phase 4 — Assemble + deliver

Produce the review: **verdict** (`ready` / `needs-changes` / `blocked`) + **findings**
(severity-sorted, cited, with fixes) + **strengths** + **uncertainty / what couldn't be
verified**. Deliver to the caller; inside `dev-task`, hand it to the verify stage. Any GitHub
post is a **draft** awaiting approval — never automatic.

## Relationship to dev-task and the Judge

- **dev-task** (autonomous_dev_skill): this skill is its **verify-stage reviewer gate** — a
  light, inline pass before the draft PR. dev-task owns isolation/execute; this owns the review.
- **company-judge**: the company-brain sibling. Shares `review-methodology.md`; differs in
  corpus (company STANDARDS/DECISIONS vs the project's own docs) and role (company vs individual).

## Examples

- *"review my feat/x branch before the PR"* → Phase-1 gate (clean), reads the repo's CLAUDE.md,
  flags **Critical** "new API client doesn't handle a 429 — will crash the heartbeat", **Minor**
  "duplicated parse helper — reuse `parse_iso`", verdict `needs-changes`.
- *dev-task verify stage* → runs this gate + review; a clean `ready` lets the pipeline draft the PR.

## Notes

- If the change touches the BrunOS code repo or vault → refuse and say why (the `code-sync`
  ff-only hazard). Point the work at a project worktree instead.
- No project standards doc + no tests → say so in the uncertainty section rather than implying
  the review was exhaustive.
