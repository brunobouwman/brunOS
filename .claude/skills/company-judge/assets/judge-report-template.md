<!--
Company Judge review output. Write to Memory/digests/judge/<artifact-slug>-<YYYY-MM-DD>.md
with full frontmatter. Fill every <…> with real evidence + citations. Delete this comment.
External posting of this review is draft-only + Full-tier-approved.
-->
---
type: digest
created: <YYYY-MM-DDThh:mm-03:00>
updated: <YYYY-MM-DDThh:mm-03:00>
tags: [judge, review, <artifact-type>]
status: active
---

# Judge Review — <artifact title> (<YYYY-MM-DD>)

**Artifact:** <PR #/path/decision title> · **Type:** <code | proposal | plan | decision | draft | output>
**Requested by:** <caller> · **Tier:** <Full | Exec | Dept> · **Corpus:** STANDARDS.md @ <rev>, DECISIONS.md @ <rev>
**Verdict:** **<ready | needs-changes | blocked>**

> <one-paragraph rationale for the verdict>

## Findings

_Severity-sorted. Each cites a STANDARDS § or a DECISIONS record. "citation: none" = a suggestion, not a violation._

| Severity | Rubric | Finding | Citation | Suggested fix |
|---|---|---|---|---|
| 🔴 Critical | <1–6> | <what's wrong> | <STANDARDS §… / DECISIONS YYYY-MM-DD - …> | <fix> |
| 🟠 Important | <…> | <…> | <…> | <…> |
| 🟡 Minor | <…> | <…> | <…> | <…> |

## Strengths

- <what the work does well — at least one, when true>

## Uncertainty / missing evidence

- <what the Judge could not verify + what it would need to verify it>
- <unverified "done/passes" claims, per evidence-before-claims>

## Conflicts (routed to Full-tier)

- <standard-vs-decision or standard-vs-standard conflicts; "none" if none>

## Action

- **Internal:** written to `Memory/digests/judge/<slug>-<date>.md`.
- **External:** <none | DRAFT prepared, awaiting Full-tier approval before any post>.
- **Override:** <if the requester pushed back on a finding, record their reasoning here>
