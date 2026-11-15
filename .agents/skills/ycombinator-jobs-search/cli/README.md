# ycombinator-jobs-cli

Zero-runtime-dependency Bun CLI for Y Combinator job pages. **Personal use only** (see `../SKILL.md`).

```bash
bun install          # dev types only
bun run src/cli.ts search -l india --format table
bun run src/cli.ts detail karat-financial/gD75RKp-founding-security-engineer --format plain
bun run typecheck
bun run test         # network-free: trimmed captured pages + mocked fetch
```

Fixtures in `tests/fixtures/` are trimmed copies of pages captured on 2026-11-11. Endpoints and parsing anchors: `../url-reference.md`.
