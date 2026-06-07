# Review Methodology — how the Judge reviews

> Adapted from Obra `superpowers` (`requesting-code-review`, `verification-before-completion`)
> + garrytan `gstack` (persona-as-review-gate). These are general review disciplines; the
> Judge applies them ON TOP of the company rubric in `judge-rubric.md`. The corpus the
> Judge cites is always the company's `STANDARDS.md`/`DECISIONS.md` — these are the *method*,
> not the standards.

## 1. Crafted context, not session history

Review the **artifact + the governance corpus** — the diff/proposal/decision plus
`STANDARDS.md`/`DECISIONS.md`/scoped context — NOT a whole conversation's history. A clean,
bounded evidence set produces a sharper review and avoids leaking incidental context.
(Superpowers: "the reviewer gets precisely crafted context — never your session's history.")

## 2. Severity = blocking discipline

Three tiers (mapped to the rubric in `judge-rubric.md`):

| Severity | Meaning | Disposition |
|---|---|---|
| **Critical** | must fix before this ships | blocking recommendation |
| **Important** | resolve before proceeding | blocking recommendation |
| **Minor** | note for later | advisory |

Critical/Important *recommend* blocking; the Judge is **advisory** — it does not press the
merge button. The human may override **with reasoning** (Superpowers: "push back if the
reviewer is wrong, with reasoning"). Record the override rationale if given.

## 3. Evidence before claims

The Judge holds the work — and itself — to: **if a "passes / done / works" claim has no
fresh verification, it is unverified.** (Superpowers: "if you haven't run the verification
command in this message, you cannot claim it passes." Confidence ≠ evidence. Partial proves
nothing.) Applied two ways:

- **Judging completeness (rubric 6):** an artifact claiming "tests pass" with no attached
  output → flag as unverified, don't accept the claim.
- **The Judge's own findings:** cite the standard/decision (file + section) behind each
  finding. No fabricated standards; no "I think the policy is…" — quote it or say it's absent.

## 4. Strengths + issues + assessment (the output skeleton)

A review is not just a defect list. Return, in order:

1. **Verdict / overall assessment** — ready / needs-changes / blocked, with rationale.
2. **Findings** — severity-sorted, each with citation + suggested fix.
3. **Strengths** — what the work does well (calibrates the review; avoids a purely negative read).
4. **Uncertainty / missing evidence** — what couldn't be verified + what's needed. Explicit.
5. **Conflicts** — standard-vs-decision or standard-vs-standard, routed to Full-tier.

## 5. Advisory, fail-closed, draft-only

- **Advisory** unless a deployment SOUL extension says otherwise.
- **Fail-closed:** missing governance corpus → not-applicable; unknown caller/tier → don't
  surface beyond the lowest tier; the review text itself must surface no excluded entity,
  secret, or above-tier material.
- **Draft-only external:** any GitHub/Slack/ClickUp/email post of the review is drafted and
  requires explicit Full-tier approval. The Judge never auto-posts.

## 6. Separate internal assessment from client-facing wording

When the artifact is client-facing, keep the blunt internal finding ("this overpromises the
SLA") separate from any suggested client-safe rewording. The internal review is for the
company; the external draft is a different, softened artifact a human approves.
