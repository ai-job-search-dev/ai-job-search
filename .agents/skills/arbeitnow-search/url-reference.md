# Arbeitnow - URL Reference

Recorded 2026-11-11 during `/add-portal` reconnaissance.

## Access

- `robots.txt`: `User-agent: *` with empty `Disallow:` (plus `/*?__hstc`) - everything allowed.
- API `meta.terms`: "This is a free public API for jobs, please do not abuse. I would appreciate linking back to the site. By using the API, you agree to the terms of service present on Arbeitnow.com". Terms section 11: API provided "as is" and "as available". No prohibition on automated access.
- UA: `Mozilla/5.0 (compatible; arbeitnow-cli/1.0)`. No challenge observed.

## Endpoints

| Purpose | URL |
|---------|-----|
| Job list | `GET https://www.arbeitnow.com/api/job-board-api?page=<n>` (300 per page, newest first, updated hourly) |
| Visa filter | add `visa_sponsorship=true` (verified: page 1 shrank 300 -> 275; field not echoed) |
| Remote | `remote=true` is **ignored** (still 300 rows, 14 remote) - filter on the `remote` field instead |
| Posting page | `https://www.arbeitnow.com/jobs/companies/<company>/<slug>` (UK: `https://www.arbeitnow.co.uk/...`) |

No keyword or location parameters were found; the docs page is https://www.arbeitnow.com/blog/job-board-api.

## API response

`{ data: [...], links: { first, last (null), prev, next }, meta: { current_page, per_page (300), from, to, terms, info } }`

Per posting: `slug`, `company_name`, `title`, `description` (HTML), `remote` (bool), `url`, `tags` (string[]), `job_types` (string[], often German), `location`, `created_at` (epoch seconds). No total count.

## Posting page

`<script type="application/ld+json">` holding `{ "@context", "@graph": [ { "@type": "JobPosting", title, identifier, datePosted, validThrough, employmentType, hiringOrganization{name,url}, directApply, jobLocation{address{postalCode, addressLocality, addressCountry}}, description (HTML), image } , ... ] }`.
