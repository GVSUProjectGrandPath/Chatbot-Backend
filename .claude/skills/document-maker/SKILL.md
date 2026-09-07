---
name: document-maker
description: >
  Generate professional software/tech work documentation by combining repo analysis with a short user interview. Use this skill whenever someone wants to write or generate: a project status update, progress report, technical design doc (TDD/RFC/spec), or a handoff/knowledge-transfer (KT) document. Trigger even on casual phrasings like "write up what I've been working on", "document this feature", "make a KT doc for this", "write a status update for my PM", "create a design doc", or "I need to hand off this project". If the user mentions a codebase, a feature, or a project and wants any kind of written summary or spec, use this skill.
---

# Document Maker

Generate polished, professional software/tech work documents by (1) analyzing the available repo/code context and (2) interviewing the user to fill in gaps.

## Supported Document Types

| Type | When to use |
|---|---|
| **Status Update / Progress Report** | User wants to communicate project state to stakeholders, PM, or team |
| **Technical Design Doc (TDD/RFC/Spec)** | User is designing a system, feature, or API and needs a written spec |
| **Handoff / Knowledge Transfer (KT) Doc** | User is handing off a project, going on leave, or onboarding someone |

---

## Workflow

### Step 1 — Identify Document Type

If the user hasn't been explicit, infer the doc type from their phrasing. If ambiguous, ask:
> "Which type of document are we making: a **status update**, a **technical design doc**, or a **handoff/KT doc**?"

### Step 2 — Repo/Code Analysis (Do This First, Silently)

Before asking the user anything, extract as much context as you can from available sources:

- **If a repo path or files are provided**: scan README, recent commit messages, PR descriptions, folder structure, key config files (package.json, pyproject.toml, Dockerfile, etc.), and any existing docs.
- **If no repo is provided**: ask the user to paste relevant code snippets, a README, or a brief description of the project before proceeding.

Use this to pre-fill: project name, tech stack, what it does, recent changes, key components/modules, dependencies.

### Step 3 — Targeted Interview

Ask only what you couldn't infer. Keep it to **one focused message** with 3–6 questions max. Tailor questions to the doc type (see templates below). Avoid asking things you already know from the repo.

### Step 4 — Generate the Document

Write the document using the appropriate template. Output it as a well-formatted Markdown document. Offer to export as `.docx` or `.md` file if the user wants to save it.

---

## Document Templates

Read the relevant reference file for the full template and field guidance:

- **Status Update** → `references/status-update.md`
- **Technical Design Doc** → `references/technical-design-doc.md`
- **Handoff / KT Doc** → `references/handoff-kt-doc.md`

---

## Interview Question Banks

Use these as a guide — pick only the questions the repo analysis didn't already answer.

### Status Update
- What time period does this cover? (sprint, week, milestone?)
- What's the current state — on track, blocked, or at risk?
- What were the key accomplishments this period?
- What's coming up next?
- Any blockers, risks, or decisions needed from stakeholders?
- Who is the audience? (PM, engineering lead, exec, whole team?)

### Technical Design Doc
- What problem are we solving and why now?
- What are the constraints or non-goals?
- Are there alternative approaches you considered?
- What does success look like? Any metrics?
- Who are the key stakeholders or reviewers?
- What's the rough timeline or phasing?

### Handoff / KT Doc
- Who is taking over, and what's their background?
- What's the handoff date / urgency?
- What are the most common tasks or operations the new owner will do?
- Are there any known issues, gotchas, or tech debt to flag?
- What context is NOT in the codebase (tribal knowledge, history, decisions)?
- Any access, credentials, or oncall setup they'll need?

---

## Quality Standards

- Write in **clear, professional prose** — no fluff, no filler
- Use headers, bullet points, and tables where they aid scanability
- Be specific — avoid vague phrases like "improved performance"; say "reduced p99 latency from 400ms to 80ms"
- Flag gaps clearly with `[TODO: ...]` rather than making things up
- Match the audience's technical level (exec summary vs eng spec)