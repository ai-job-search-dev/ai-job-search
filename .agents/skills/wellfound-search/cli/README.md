# wellfound-cli

Zero-runtime-dependency Bun CLI for Wellfound role pages. **Personal use only** (see `../SKILL.md`).

```bash
bun install          # dev types only
bun run src/cli.ts search -q "engineering manager" -l bangalore --format table
bun run src/cli.ts detail 4475538-engineering-manager-backend --format plain
bun run typecheck
bun run test         # network-free: trimmed captured pages + mocked fetch
```

Fixtures in `tests/fixtures/` are trimmed copies of pages captured on 2026-11-11. Endpoints and parsing anchors: `../url-reference.md`.
