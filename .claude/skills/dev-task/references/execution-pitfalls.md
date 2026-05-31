# dev-task — execution pitfalls & pre-PR checklist (SEED)

> **Status: seed.** The fuller, authoritative version is being produced by the
> code-sync-hardening session (it learned the same lesson: the bot bricking a
> repo). When that lands, replace this file with it. The grep/check one-liners
> below are the deterministic gate the worker already enforces + the human-review
> companion list.

## What the worker enforces automatically (deterministic gate in `run_stage.py`)

Before any commit/PR, the execute stage refuses to proceed if the diff contains:
- **Unresolved merge-conflict markers** — `<<<<<<<`, `=======`, `>>>>>>>`.
- **Secret-looking strings** — `-----BEGIN … PRIVATE KEY`, `xoxb-`/`xoxp-`/`xapp-`,
  `sk-ant-`/`sk-proj-`, `aws_secret_access_key`.
- **No changes at all** — if execute produced nothing, there's nothing to PR.

## Pre-PR checklist (one-liners — run in the worktree)

```bash
# 1. Clean of conflict markers
! git -C "$WT" diff HEAD | grep -nE '^(<{7}|={7}|>{7})' || echo "CONFLICT MARKERS"

# 2. Nothing secret / no .env staged
git -C "$WT" diff --cached --name-only | grep -E '(^|/)\.env|\.pem$|\.key$|secrets?/' && echo "SECRET PATH STAGED"

# 3. No stray debug left behind
git -C "$WT" diff HEAD | grep -nE '\b(console\.log|debugger|binding\.pry|import pdb|breakpoint\()' || true

# 4. The branch is OFF main, not main itself
test "$(git -C "$WT" rev-parse --abbrev-ref HEAD)" != "main" || echo "ON MAIN — ABORT"

# 5. Validations actually ran (don't trust 'looks done')
#    → run the plan's stated build/lint/test commands; a green tree is the gate.
```

## Hard-won pitfalls (the reasons this skill is shaped the way it is)

1. **Never operate on the prod BrunOS code repo / vault.** A stray branch there
   makes `code-sync`'s `git pull --ff-only` die *silently* — the exact failure
   that motivated this skill. The target guard refuses it; don't override it.
2. **Worktree, not in-place branch.** The main checkout must stay pristine so
   nothing the dev agent does can desync the working tree.
3. **Push before PR; treat a failed `gh pr create` as recoverable.** If the push
   succeeded the branch is safe even when PR creation 422s (private-repo draft
   limitation) — report it, don't pretend success, don't lose the branch.
4. **Keep the worktree on failure.** Don't auto-clean a failed run; the worktree
   + trace log are how we debug reliability.
5. **A green agent summary is not proof.** Trust the validation commands' exit
   codes, not the model saying "done".
