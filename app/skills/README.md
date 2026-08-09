# Agent Skills

Place human-authored, domain-specific skills under this directory. Each skill is a folder with a
`SKILL.md` containing YAML frontmatter (`name`, `description`) followed by the method body. The
loader keeps metadata in the normal prompt context and loads the body only after a match. Optional
files under `resources/` are loaded only when a caller explicitly requests them.

Skills provide method guidance; deterministic tools remain responsible for evidence, prices,
eligibility, and calculations. Automatically distilled strategy memory lives separately in
`app/memory/strategy.py`.
