# apify-jobs-search-cli

Zero-runtime-dependency Bun CLI that runs Apify Store job-board actors on the free plan. See `../SKILL.md`; boards live in `../actors.json`.

```bash
bun install          # dev types only
bun run src/cli.ts boards
bun run typecheck
bun run test         # network-free: mocked fetch, temp ledger
```

`src/budget.ts` is shared byte-for-byte with `firecrawl-fetch` (enforced by
`tests/test_scrape_budget_sync.py` at the repo root): edit one, copy it over the other.
