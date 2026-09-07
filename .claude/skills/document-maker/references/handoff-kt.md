# Handoff / Knowledge Transfer (KT) Doc Template

## Structure

```markdown
# [Project / Service Name] — Handoff & Knowledge Transfer Doc
**Outgoing Owner:** [Name]
**Incoming Owner:** [Name]
**Handoff Date:** [Date]
**Last Updated:** [Date]

---

## TL;DR
[2–3 sentences: what this project/service does, its criticality, and the most important thing the new owner should know.]

## Project Overview
- **What it does**: [Plain-English description]
- **Who uses it / depends on it**: [Users, teams, downstream services]
- **Tech stack**: [Languages, frameworks, key libraries]
- **Criticality**: [Critical (on-call) / High / Medium / Low]

## Codebase Map
[Help the new owner navigate the repo. Describe key directories/files and what they do.]

| Path | What's in it |
|------|-------------|
| `/src/core/` | [Description] |
| `/src/api/` | [Description] |
| `config/` | [Description] |
| ... | ... |

## How to Run & Deploy
### Local Setup
```bash
# Step-by-step commands to get it running locally
```

### Deployment
[How to deploy. CI/CD pipeline, manual steps, environments (staging/prod), rollback procedure.]

## Common Tasks
[The top 3–5 things the new owner will do regularly.]

### [Task 1: e.g. Deploying a new release]
[Steps]

### [Task 2: e.g. Rotating secrets]
[Steps]

## Oncall / Incident Response
- **Runbook location**: [Link]
- **Alerting**: [Where alerts go, who to page]
- **Common incidents and how to handle them**:
  - [Symptom] → [What to check / do]

## Key Integrations & Dependencies
| Service / System | What for | Owner / Contact |
|-----------------|----------|----------------|
| [Service] | [Why we depend on it] | [Team/person] |

## Access & Credentials
- [ ] [System] — request access via [process/link]
- [ ] [Secret/credential] — stored in [vault/location]
- [ ] [Dashboard/tool] — [link, how to get access]

## Known Issues & Tech Debt
| Issue | Severity | Notes / Workaround |
|-------|---------|-------------------|
| [Issue] | H/M/L | [Context] |

## Tribal Knowledge & History
[Things NOT in the codebase that the new owner must know. Design decisions, historical context, weird quirks, why something was done a certain way.]

- **[Topic]**: [Explanation]
- ...

## Key Contacts
| Person | Role | When to contact |
|--------|------|----------------|
| [Name] | [Role] | [What they know / own] |

## Resources
- Docs: [Links]
- Design docs: [Links]
- Dashboards: [Links]
- Slack channels: [#channel-name]
- Tickets/backlog: [Link]
```

## Field Notes

- **Tribal Knowledge**: This is the highest-value section — prioritize anything not in the code
- **Common Tasks**: Write these as step-by-step runbooks, not descriptions
- **Access**: Use checkboxes so the incoming owner can track what they've completed
- **Known Issues**: Be honest — surprises after handoff erode trust