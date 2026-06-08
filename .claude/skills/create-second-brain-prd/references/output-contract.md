# Onboarding Spec — output contract

> The exact bundle this skill emits and `bootstrap-brain` consumes. This is the **handoff
> interface** between elicit (this skill) and build (bootstrap). Keep them in sync.

The spec is written to the output dir (default `.agent/plans/brain-spec/<slug>/`):

```
brain-spec/<slug>/
  brain-config.json          # the machine-readable knobs (see below)
  variables.yaml             # per-brain parameterization (names, scope, role, federation)
  seeds/                      # the seed content, filled from the answers
    SOUL.md
    USER.md            (individual)   |  COMPANY.md           (company)
    MEMORY.md          (individual)   |  LINMEMORY.md / COMPANY_MEMORY.md (company)
    HEARTBEAT.md, HABITS.md (individual)
    STANDARDS.md, DECISIONS.md, ROUTINES.md, ACCESS_POLICY.md,
      _excluded-people.md, _brain-filing-rules.md            (company)
  folder-taxonomy.md         # the Memory/ subfolders (from memory categories + departments)
  integrations.md            # per enabled integration: env var NAMES + auth method (NO secret values)
  README.md                  # human summary + the exact next command
```

## `brain-config.json` keys this skill sets (base = `brain_config.DEFAULTS`)

- `role`: `individual` | `company`
- `timezone`, `default_language`
- `action_surface` / proactivity behavior toggles (from the proactivity level)
- `reflection.federation`: `true` (producer) | `false` (singleton); + federation role + scope tag
- integrations **enabled** (derived from platforms + priority) — names only
- cadences (reflection/dream/heartbeat/comms-capture), per role + proactivity
- `personas` (company only — which governed skills are enabled + their tiers/scopes)
- `channels` (chat / comms-capture registry, if provided)
- `comms_capture`, `notify`

## What this skill must NOT put in the spec

- **No secret values** — only env var names + auth method (the operator fills secrets at build).
- **No build narrative / phase plan** — the build is `bootstrap-brain`.
- **No invented governance** — don't fabricate excluded names, standards, or decisions; seed
  only what the answers provide; leave the rest as reviewed-empty.

## Handoff to `bootstrap-brain`

1. The operator runs **`bootstrap-brain` (greenfield)** pointed at `brain-spec/<slug>/`.
2. Bootstrap's greenfield phase **loads this spec** and provisions the **uniform, secure stack**
   parameterized by it: identity seeds (from `seeds/`), `brain-config.json`, the folder
   taxonomy → brain-local `vault-structure`/`memory-search` skills (R3), the security hooks +
   path-boundary guard (R1/R2), integrations (R5), units (`gen_schedules.py`), monitoring.
3. **`diagnose-brain` validates** the result is complete + secure.

## The invariant that kills divergence

The spec varies only **config + content**. The **build is identical every time** (bootstrap)
and **verified** (diagnose). So two people filling the same requirements get the same
working, secure brain — differing only where they *chose* to differ. A spec **cannot** produce
an insecure or incomplete brain: it never describes the build, only the parameters.
