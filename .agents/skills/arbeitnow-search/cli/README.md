# arbeitnow-cli

Zero-runtime-dependency Bun CLI for Arbeitnow's free public job-board API.

```bash
bun install          # dev types only
bun run src/cli.ts search -q backend --visa --format table
bun run src/cli.ts detail bunch/senior-backend-engineer-berlin-433276 --format plain
bun run typecheck
bun run test         # network-free: captured API page + job page, mocked fetch
```

Fixtures in `tests/fixtures/` are trimmed copies captured on 2026-11-11. See `../SKILL.md` and `../url-reference.md`.
