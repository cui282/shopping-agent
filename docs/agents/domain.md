# Domain Docs

This is a single-context repository. Engineering skills should use these rules when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repository root.
- **`docs/adr/`** for architecture decisions that touch the area being changed.

If either location does not exist, proceed silently. Do not suggest creating empty documentation. The `/domain-modeling` workflow creates or updates these files when terminology or decisions are actually resolved.

## File structure

```text
/
|-- CONTEXT.md
|-- docs/
|   `-- adr/
|-- app/
`-- frontend/
```

## Use the glossary's vocabulary

When output names a domain concept in an issue, proposal, hypothesis, or test, use the term defined in `CONTEXT.md`. Do not drift to synonyms the glossary explicitly avoids.

If a required concept is missing, first reconsider whether the new term is necessary. If it represents a real gap, note it for `/domain-modeling`.

## Flag ADR conflicts

If proposed work contradicts an existing ADR, surface the conflict explicitly rather than silently overriding the decision.
