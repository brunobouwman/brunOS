# My Second Brain - Requirements Template

> Fill this out during the workshop (Section 1.4). Your answers feed directly into the `/create-second-brain-prd <path to this file>` command, which generates your personalized build plan.

---

## 1. About You

- **Name:** Bruno Bouwman
- **Role/Title:** Ai Engineer
- **What I do daily** (1-2 sentences): I divide myself between my company: ai agents for improving sales for labs and clinics through customer service + free lancing with my girlfriend Lisa (developing/consulting AI agents and automations as a service) + learning more about AI engineering for my transition
- **Timezone:** GMT-3
- **Memory vault folder name:** BrunOS
- **Using Obsidian?** [X] Yes [ ] No

---

## 2. Your Platforms

Check every platform you actively use and fill in the specific tool:

- [x] Email (e.g., Gmail, Outlook): Gmail
- [x] Calendar (e.g., Google Calendar, Outlook Calendar): Google Calendar
- [x] Task Management (e.g., Asana, Linear, Todoist, Jira): ClickUp
- [x] Chat/Messaging (e.g., Slack, Discord, Teams): Slack
- [x] Notes/Documents (e.g., Notion, Obsidian, Google Docs): Obsidian
- [ ] Cloud Storage (e.g., Google Drive, Dropbox, OneDrive): \_\_\_
- [x] Code Hosting (e.g., GitHub, GitLab): Github
- [x] Community (e.g., Circle, Discord server, Mighty Networks): X (twitter) through RSS/Nitter fallback
- [ ] CRM (e.g., HubSpot, Salesforce, Pipedrive): \_\_\_
- [ ] Other: \_\_\_

---

## 3. Top Tasks for AI

List 3-5 tasks you'd want your second brain to handle proactively:

Examples:

- Draft email replies to important messages
- Track deadlines and remind me before they're due
- Summarize what happened in Slack while I was away
- Monitor community for questions that need my attention
- Keep my meeting notes organized and searchable

**My list:**

1. Summarize what happened in Slack while I was away
2. Track tasks, goals, ideas, leads and responsibilities to help me manage my work and life
3. Monitor community and specific sources for continuous news aggregation to keep me up to date with innovations filtering away some noise
4. day/month/week/year structured/organized planning
5. Open Issues and PRs on GH so that I can act as a supervsior

---

## 4. Proactivity Level

How bold should your agent be? Pick one:

- [ ] **Observer** - Notify only, never take action
- [ ] **Advisor** - Draft things for my review, but never send or post
- [x] **Assistant** - Act on low-risk items (log notes, organize files), ask for high-risk
- [ ] **Partner** - Act autonomously on most things, ask only for irreversible actions

---

## 5. Security Boundaries

What should your agent NEVER do without explicit permission?

- [x] Send emails or messages on my behalf
- [x] Post to social media
- [ ] Modify files outside the memory vault
- [x] Access financial data or make purchases
- [x] Delete anything
- [ ] Other: \_\_\_

---

## 6. Memory Categories

What types of knowledge matter most to you? Check all that apply and add your own:

- [x] Meeting notes and decisions
- [x] Project status and progress
- [x] Client/customer information
- [x] Research and learning notes
- [x] Personal goals and habits
- [x] Content ideas and drafts
- [x] Team context (who does what, preferences, timezones)
- [ ] Other: \_\_\_

---

## 7. Infrastructure

- **Operating System:** [ ] Windows [X] macOS [ ] Linux
- **Deployment:** [ ] Local only [X] Local + cloud server (VPS)
- **Existing tools I already have set up:** I already use obsidian but with a different vault structure, which I intend to change and I have claude code subscription integrated with ClickUp

  (e.g., "I already use Obsidian", "I have a DigitalOcean droplet", "I'm comfortable with the terminal")

---

## 8. Integration Priority

Rank your top 3 integrations to build first (from your answers in Section 2):

1. Slack
2. Github
3. ClickUp

---

## 9. Brain Role & Federation

What KIND of brain is this, and does it connect to others? (This gates the whole build.)

- **Role:**
  - [ ] **Individual** — a personal brain serving one person (drafts in their voice).
  - [ ] **Company** — an institutional brain serving multiple people (neutral voice, tiered access, governance-first).
- **Federation:**
  - [ ] **Singleton** — stands alone; no federation. (Simplest. Most solo clients.)
  - [ ] **Producer** — an individual brain that **shares cleared, work-scoped knowledge into a company brain.** Name the company brain it feeds + the export scope tag: \_\_\_
  - [ ] **Consumer** — this **is** a company brain that **receives** from producer brains. List the producer brains/scopes it consumes: \_\_\_
- **This brain is for:** [ ] me  [ ] a teammate  [ ] a client (company): \_\_\_

---

## 10. Company / Client block — ONLY if Role = Company

Skip if this is an individual brain.

- **Company name / brain name:** \_\_\_ / \_\_\_  (e.g. "Protostack" / "LinOS")
- **Company slug + default export scope tag:** \_\_\_
- **Departments / teams:** \_\_\_
- **Access tiers — who sees what** (fail-closed by default; deny unknown):
  - **Full** (all knowledge): \_\_\_
  - **Exec** (cross-department synthesis): \_\_\_
  - **Dept** (own department + shared): \_\_\_
- **Standards seeds** — durable company rules/values to seed `STANDARDS.md` (engineering, client-work, decision): \_\_\_
- **Known decisions** — prior decisions to seed `DECISIONS.md` (with reversal triggers): \_\_\_
- **Personas to enable** (governed skills): [ ] consolidator [ ] judge [ ] leadership-digest [ ] gap-analyst [ ] standards-review [ ] query
- **Excluded entities** — names that must NEVER appear in surfaced output (reviewed, not invented): \_\_\_
- **Channels** (for chat / comms-capture): surface + id + audience + ingestion mode (`ask-only`/`ingest-and-answer`/`digest-only`): \_\_\_

---

> After filling this out, run: `/create-second-brain-prd <path to this file>`. It emits the
> **onboarding spec** (config + seeds) — NOT a build plan. Then run `bootstrap-brain` against
> that spec to build the uniform, secure stack, and `diagnose-brain` to validate it.
