# berlinstartupjobs-cli

Zero-runtime-dependency Bun CLI for Berlin Startup Jobs, using the site's public WordPress REST API.

```bash
bun install          # dev types only (typescript, @types/bun)
bun run src/cli.ts search -q "engineering manager" --format table
bun run src/cli.ts detail 452724 --format plain
bun run typecheck
bun run test         # network-free: captured fixture + mocked fetch
```

See `../SKILL.md` for flags and `../url-reference.md` for endpoints and response fields.
`tests/fixtures/search-posts.json` is a trimmed copy of a live response captured on 2026-11-11.
