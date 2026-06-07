# Judge Rubric — the six checks, expanded

> The canonical rubric is the **"Judge Rubric" section of the company's `STANDARDS.md`**.
> This file expands each of the six dimensions with: what to look for, the citation it
> must produce, the default severity, and an example finding. When the company's
> `STANDARDS.md` adds or changes a rubric item, that file wins — re-read it each run.

Each finding MUST carry a citation: a `STANDARDS.md` section (`§<section>`) or a
`DECISIONS.md` record (`<YYYY-MM-DD - title>`). If none exists, write
`citation: none — no governing standard` and treat it as a *suggestion*, not a violation
(and consider routing to `company-standards-review`).

---

## 1. Access / scope safety  ·  default severity: **Critical**

**Look for:** work that crosses an access tier or a scope boundary — surfacing
department-private material to a broader tier, an integration that reads/writes outside its
declared scope, a client artifact mixing another client's context, a brain-to-brain
exchange that ignores the consumer read scope.

**Citation:** `ACCESS_POLICY.md` tiers + `STANDARDS.md §Operating Principles` /
`§Client Work Standards`.

**Example:** *Critical — the proposal attaches Client B's pricing inside Client A's
deck. Violates STANDARDS §Client Work "keep client context scoped to authorized users."
Fix: remove Client B references; regenerate from Client A sources only.*

## 2. Privacy & excluded-entity safety  ·  default severity: **Critical**

**Look for:** secrets/credentials in the artifact, private personal data, or any name in
`_excluded-people.md`. Privacy checks are **acceptance criteria, not cleanup** — a privacy
miss blocks regardless of how good the rest is.

**Citation:** `STANDARDS.md §Operating Principles` ("privacy boundaries are product
boundaries") + `_excluded-people.md`.

**Example:** *Critical — the PR commits a config with a live API token. Violates
STANDARDS §Engineering "security/privacy checks are acceptance criteria." Fix: rotate the
token, move it to env, add it to the secret-scan.*

## 3. Consistency with standards  ·  default severity: **Important**

**Look for:** divergence from `STANDARDS.md` — architecture changed without an explicit
reason, an integration that fails *open* on unknown input, automation with no
observability, a client-facing draft not flagged for human review.

**Citation:** the specific `STANDARDS.md §` violated.

**Example:** *Important — the new consumer integrates captures with unknown
`share_status`. Violates STANDARDS §Engineering "every integration must fail closed on
unknown identity, scope, or source status." Fix: treat unknown status as not-cleared; skip.*

## 4. Consistency with prior decisions  ·  default severity: **Important** (Critical if it reverses an active decision)

**Look for:** work that contradicts an **active** record in `DECISIONS.md`. Before flagging
"drift," check the decision's **reversal triggers** — if a trigger has demonstrably
occurred, the change may be legitimate (note that). If it reverses an active decision with
no trigger met, that's **Critical**.

**Citation:** the `DECISIONS.md` record (`<YYYY-MM-DD - title>`) + its reversal triggers.

**Example:** *Critical — the plan moves brain-to-brain transport to MCP. Reverses
DECISIONS "2026-06-06 - brain-to-brain transport is scoped RPC not MCP"; no listed reversal
trigger has occurred. Fix: keep scoped RPC, or open a decision-reversal proposal with
evidence a trigger fired.*

## 5. Evidence quality & citations  ·  default severity: **Important** (Minor for low-stakes claims)

**Look for:** substantive claims with no source ("evidence over memory"). Numbers,
client commitments, security/scope assertions, and "X is done" all need a source. Apply the
**evidence-before-claims** rule (see `review-methodology.md`): a "tests pass / it works"
claim with no fresh verification output is unverified.

**Citation:** `STANDARDS.md §Operating Principles` ("evidence over memory") /
`§Decision Standards`.

**Example:** *Important — "the migration is backward-compatible" with no test run or
reference. Unverified per STANDARDS §"evidence over memory." Fix: cite the compat test
output, or downgrade the claim to "believed compatible, unverified."*

## 6. Operational completeness  ·  default severity: **Minor → Important**

**Look for:** missing **owner**, **next step**, **deadline**, or **verification**. A
decision record missing owner/rationale/reversal-triggers; a plan with no acceptance
criteria; a "done" with no proof command run.

**Citation:** `STANDARDS.md §Decision Standards` / `§Engineering Standards`.

**Example:** *Minor — the decision record omits reversal triggers. STANDARDS §Decision
Standards requires "owner, date, rationale, expected impact, and reversal triggers." Fix:
add the triggers that would revisit this.*

---

## Severity quick-map

| Rubric dimension | Default | Escalates to Critical when… |
|---|---|---|
| 1 Access/scope | Critical | (already) |
| 2 Privacy/excluded | Critical | (already) |
| 3 Standards | Important | the standard breached is a security/privacy/money one |
| 4 Decisions | Important | it reverses an **active** decision with no trigger met |
| 5 Evidence | Important | the unsourced claim drives a client/security/money action |
| 6 Completeness | Minor | the missing piece is the **verification** of a risky change |

**Rule of thumb:** privacy, access, and "reverses an active decision" are the three things
that turn a finding Critical. Everything else is Important or Minor.
