# Y Combinator Jobs - URL Reference

Recorded 2026-11-11 during `/add-portal` reconnaissance.

## Access

- `www.ycombinator.com/robots.txt` (`User-agent: *`): disallows `/verify/*`, `/library?*` (except `categories=`), `/companies?*`; **allows** `/jobs/...` and `/companies/<co>/jobs/<key>`.
- YC Terms of Use prohibit robots/scraping/data mining -> personal-use-only warning in SKILL.md.
- `www.workatastartup.com`: robots.txt allows all, but `/jobs` returns HTTP 406 to tools and listings need a YC account. Not used.
- UA: `Mozilla/5.0 (compatible; ycombinator-jobs-cli/1.0)`. No challenge observed.

## Endpoints

| Purpose | URL |
|---------|-----|
| Role page | `https://www.ycombinator.com/jobs/role/<role>` |
| Role + location | `https://www.ycombinator.com/jobs/role/<role>/<location>` |
| Job page | `https://www.ycombinator.com/companies/<company>/jobs/<key>` (key like `gD75RKp-founding-security-engineer`) |
| Landing | `https://www.ycombinator.com/jobs` |

Unknown role slugs return **200 with the software-engineer list** under the requested URL (silent fallback).
Detect it by checking the slug against `props.jobRoles`. Unknown location slugs render the generic page;
detect by comparing `props.location.slug` (generic page: `{ slug: "local", is_local: true }`).

## Role page structure (Inertia.js)

`<div ... data-page="{...}">` - HTML-entity-encoded JSON `{ component: "WaasJobListingsPage", url, props }`.

`props`: `title`, `location {slug,name,is_local}`, `jobCategory`, `jobLocations [{slug,name}]`, `jobRoles [{slug,name}]`, `roleOptions`, `jobPostings[]`.

`jobPostings[]`: `id` (number), `title`, `url` (`/companies/<co>/jobs/<key>`), `location`, `type`, `role`,
`roleSpecificType`, `prettyRole`, `salaryRange`, `equityRange`, `minExperience`, `visa`
(`US citizen/visa only`, `Will sponsor`, ...), `skills`, `companyName`, `companyBatchName`,
`companyOneLiner`, `createdAt` (relative: `9 days`, `about 1 month`, `over 2 years`), `lastActive`, `applyUrl`.

No pagination parameter.

## Job page structure

- `<script type="application/ld+json">` schema.org `JobPosting`: `title`, `url`, `description` (HTML), `datePosted`,
  `employmentType`, `hiringOrganization.name`, `baseSalary`, `jobLocation[].address`, `jobLocationType`, `applicantLocationRequirements`.
- `data-page` with `component: "WaasShowJobPage"`, `props.job` (same fields as a listing plus `description` markdown,
  `interview_process`) and `props.company` (`slug`, `name`, `website`, `team_size`, `tags`, ...).
