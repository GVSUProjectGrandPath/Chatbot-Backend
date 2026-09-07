# Technical Design Doc (TDD / RFC / Spec) Template

## Structure

```markdown
# [Feature / System Name] — Technical Design Doc
**Author(s):** [Name]
**Date:** [Date]
**Status:** Draft | In Review | Approved
**Reviewers:** [Names / teams]

---

## Overview
[1–2 paragraph plain-English description of what this is and why it matters. A new engineer should understand the gist after reading this.]

## Problem Statement
[What problem are we solving? Why is this the right time to solve it? What's the impact of not solving it?]

## Goals
- [Specific, measurable goal]
- ...

## Non-Goals
- [What this explicitly does NOT cover — important to prevent scope creep]
- ...

## Background & Context
[Any prior work, decisions, or history a reviewer needs to know. Link to related docs or tickets.]

## Design

### High-Level Architecture
[Diagram or description of the overall system design. How do the pieces fit together?]

### Key Components
#### [Component 1]
[What it does, why it's designed this way]

#### [Component 2]
...

### Data Model (if applicable)
[Schema, key fields, relationships. Use tables or code blocks.]

### API / Interface (if applicable)
[Endpoints, request/response shapes, contracts]

### Sequence / Flow (if applicable)
[Step-by-step flow for the main use case. Pseudocode or numbered steps.]

## Alternatives Considered
| Option | Pros | Cons | Why Rejected |
|--------|------|------|--------------|
| [Alt 1] | | | |
| [Alt 2] | | | |

## Rollout Plan
- **Phase 1**: [What, when, who]
- **Phase 2**: ...
- **Feature flags / gradual rollout**: [Yes/No + details]

## Testing Strategy
- Unit tests: [What will be tested]
- Integration tests: [Key scenarios]
- Load/perf testing: [If relevant]

## Risks & Mitigations
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| [Risk] | H/M/L | H/M/L | [How to mitigate] |

## Open Questions
- [ ] [Unresolved question that needs input]
- [ ] ...

## Success Metrics
[How will we know this worked? Specific metrics and targets.]

## Timeline
| Milestone | Target Date |
|-----------|-------------|
| Design approved | |
| Implementation complete | |
| Launched | |
```

## Field Notes

- **Status**: Keep this updated as the doc moves through review
- **Non-Goals**: This is one of the most valuable sections — be explicit
- **Alternatives Considered**: Shows intellectual honesty; reviewers will ask about these anyway
- **Open Questions**: Use checkboxes; resolve before marking Approved