# firecrawl-fetch-cli

Zero-runtime-dependency Bun CLI for the Firecrawl v2 API on the free plan. See `../SKILL.md`.

```bash
bun install          # dev types only
bun run src/cli.ts budget --format plain
bun run typecheck
bun run test         # network-free: mocked fetch, temp ledger
```

`src/budget.ts` is shared byte-for-byte with `apify-jobs-search` (enforced by
`tests/test_scrape_budget_sync.py` at the repo root): edit one, copy it over the other.
