# Wellfound - URL Reference

Recorded 2026-11-11 during `/add-portal` reconnaissance.

## Access

- `robots.txt` (`User-agent: *`) disallows `/search`, `/_jobs/`, `?jobId=`, `?role=`, `?jobSlug=`, auth/profile paths, among others. **Allowed:** `/role/...`, `/jobs/<id>-<slug>`, `?page=` on role pages.
- Terms of Service prohibit scraping and automated load above human browsing -> personal-use-only warning in SKILL.md.
- No login or challenge observed for role/job pages with UA `Mozilla/5.0 (compatible; wellfound-cli/1.0)`.

## Endpoints

| Purpose | URL | Notes |
|---------|-----|-------|
| Role, any location | `https://wellfound.com/role/<role-slug>` | |
| Role in a city | `https://wellfound.com/role/l/<role-slug>/<city-slug>` | Unknown pair -> 303 to `/location/<city>` |
| Remote role | `https://wellfound.com/role/r/<role-slug>` | |
| Paging | `?page=<n>` | ~20 companies per page |
| Job detail | `https://wellfound.com/jobs/<id>-<slug>` | `/jobs/<id>` alone and wrong slugs -> 404 |

## Role page structure

`<script id="__NEXT_DATA__" type="application/json">` -> `props.pageProps.apolloState.data`, an Apollo
normalized cache keyed `Type:id`:

- `StartupResult:<id>` - `name`, `slug`, `companySize`, `highConcept`, `highlightedJobListings: [{__ref: "JobListingSearchResult:<id>"}]`
- `JobListingSearchResult:<id>` - `id`, `title`, `slug`, `jobType`, `liveStartAt` (epoch seconds), `locationNames[]`, `remote`, `acceptedRemoteLocationNames[]`, `remoteConfig.kind`, `compensation` (display string), `yearsExperienceMin/Max`, `description` (markdown)
- Also `Badge:*`, `ROOT_QUERY`, `SeoRoleKeyword`.

Company for a job = the `StartupResult` whose `highlightedJobListings` references it.

## Job page structure

`<script type="application/ld+json">` with schema.org `JobPosting`: `title`, `identifier.value` (job id),
`hiringOrganization.name/sameAs`, `employmentType`, `datePosted` (ISO), `jobLocation[].address.addressLocality/addressRegion/addressCountry`,
`jobLocationType` (`TELECOMMUTE` for remote), `applicantLocationRequirements.name`, `baseSalary.currency/value.minValue/maxValue/unitText`,
`industry`, `description` (HTML). The page has no `__NEXT_DATA__`.
