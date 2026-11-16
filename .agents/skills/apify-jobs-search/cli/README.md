# apify-jobs-search-cli

Zero-runtime-dependency Bun CLI that runs Apify Store job-board actors on the free plan. See `../SKILL.md`; boards live in `../actors.json`.

```bash
bun install          # dev types only
bun run src/cli.ts boards
bun run typecheck
bun run test         # network-free: mocked fetch, temp ledger
```

`src/budget.ts` is the budget ledger and rate limiter every paid call goes through.
