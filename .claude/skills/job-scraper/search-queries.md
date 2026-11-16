# Search Queries for Job Scraper

<!-- Personalized by /setup (Nov 2026) for Vikas Kumar -->

## Installed portal CLIs (primary for `/scrape`)

`/scrape` discovers every portal skill under `.agents/skills/*/SKILL.md` and runs its CLI first. Shipped country-agnostic CLIs include `linkedin-search` and `freehire-search`; Danish demos and any skill you add with `/add-portal` are included the same way. You do **not** need a matching `site:` line below for those CLIs to run.

The `site:` query templates in this file are the **WebSearch fallback** — for portals without a CLI, company career pages, or when a CLI fails.

**Language scope:** write every query category in every language listed in your CLAUDE.md Languages table (typically 1-2, sometimes more). A posting requiring a language you have *not* declared, as a job condition, is excluded before scoring; a posting requiring a *higher level* than you declared in a language you *do* work in is flagged for your own judgment, not excluded — see `04-job-evaluation.md`'s Language Gate, the single source of truth for this rule. Translate each category's keywords rather than machine-translating word-for-word (e.g. "Frontend Developer" -> "Desarrollador Frontend", not a literal word-for-word translation) if you work in more than one language.

## Search Sites

Primary:
- **linkedin.com/jobs** - covered by the `linkedin-search` CLI. Run each plain term against every location in the Location Filter below (India cities, `Remote`, `Germany`, `Luxembourg`).
- **freehire** - covered by the `freehire-search` CLI.
- **Wellfound** - `wellfound-search` CLI (personal use only). Role-page search: use role names (`engineering manager`, `staff engineer`, `backend engineer`) with `-l bangalore|mumbai|pune|delhi|berlin` or `--remote`.
- **Y Combinator** - `ycombinator-jobs-search` CLI (personal use only). Use `-l india|remote|berlin` with `-q manager|staff|principal|backend`; keep results whose `visa` is `Will sponsor` or whose location names India/Germany.
- **Berlin Startup Jobs** - `berlinstartupjobs-search` CLI. Berlin only; use the plain terms below as `-q`.
- **Arbeitnow** - `arbeitnow-search` CLI (free public API, Germany + some UK). No server-side keyword search: pass a plain term as `-q` with `--visa` for sponsoring employers and `--pages 3`; many postings require German (Language Gate).
- Not available as CLIs: Naukri (robots.txt blocks Claude user agents), StepStone and jobs.lu (bot protection), Instahyre (Cloudflare challenge), Cutshort and germantechjobs.de (login wall), Work at a Startup (public page is the same YC feed `ycombinator-jobs-search` covers; search needs a YC login), Hirist/iimjobs (no public search API identified). Use the WebSearch fallback for these.
- **Paid, budgeted (Apify free tier):** `apify-jobs-search` covers Indeed (India, and Germany/Luxembourg via `--country`), Glassdoor, Foundit, Instahyre and a six-board remote aggregator through Apify Store actors configured in its `actors.json`. Its Naukri board ships disabled because of the robots.txt note above. Run it after the free CLIs, Priority 1-2 terms only by default, under the run budget rules in `SKILL.md` Step 1b.
- Danish portal demos (Jobindex, Jobbank, Jobdanmark, Jobnet) are installed but **disabled**: not this candidate's market.
- WebSearch-only boards: Germany germantechjobs.de, stepstone.de; Luxembourg jobs.lu, moovijob.com; India naukri.com, instahyre.com, cutshort.io.

Secondary (company career pages via Google):
- Direct Google searches with `site:` filters (e.g. `site:jobs.lever.co`, `site:boards.greenhouse.io`, `site:jobs.ashbyhq.com`) - many AI startups and fintechs post only there.

## Query Categories

Queries are grouped by priority. Languages: English only (the candidate works in English and Hindi; Indian, Berlin-tech and Luxembourg international postings are written in English). Combine each query with location terms where the site supports it.

Each priority has two kinds of block. **Plain terms** blocks are what the portal CLIs consume: one term per line, one CLI call per term, no `OR`, no `site:` prefix, and no location (the CLI's location flag carries it). Quote a multi-word phrase only if the portal's `Query syntax:` line says quoting is honoured. **WebSearch-only** blocks are Google syntax (`site:`, quotes, `OR`) for the fallback path and are never passed to a CLI.

**Organize by function, not job title.**

### Priority 1: Hands-on Engineering Leadership (backend / platform / AI)

The strongest and most desired direction: leading engineering teams while staying technical.

#### Plain terms (portal CLIs)

```
Engineering Manager
Senior Engineering Manager
Engineering Manager Backend
Engineering Manager Payments
Engineering Manager AI
Head of Engineering
Director of Engineering
```

#### WebSearch-only (`site:` fallback)

```
site:naukri.com "Engineering Manager" backend (Hyderabad OR Bangalore OR Pune OR Gurgaon OR Mumbai)
site:instahyre.com "Engineering Manager" (Hyderabad OR Bangalore)
site:wellfound.com "Engineering Manager" India
site:linkedin.com/jobs "Engineering Manager" payments India
site:jobs.lever.co OR site:boards.greenhouse.io OR site:jobs.ashbyhq.com "Engineering Manager" (Berlin OR Munich OR Luxembourg) visa sponsorship
```

### Priority 2: Staff / Principal Engineering (backend, payments, platform)

Senior individual-contributor track; the strongest card for roles abroad (visa sponsorship more common).

#### Plain terms (portal CLIs)

```
Staff Engineer
Principal Engineer
Staff Backend Engineer
Principal Backend Engineer
Staff Software Engineer Payments
Principal Software Engineer
Software Architect
```

#### WebSearch-only (`site:` fallback)

```
site:naukri.com ("Staff Engineer" OR "Principal Engineer") backend (Hyderabad OR Bangalore OR Pune)
site:linkedin.com/jobs "Staff Backend Engineer" (Berlin OR Munich) Germany
site:berlinstartupjobs.com "Staff Engineer" OR "Principal Engineer" backend
site:jobs.lu ("Senior Backend Engineer" OR "Staff Engineer" OR "Lead Engineer") payments
site:germantechjobs.de "Staff" backend Python OR Go OR Node.js
```

### Priority 3: Domain - Payments, Fintech, Commerce, Applied AI

Domain expertise searches; catch roles whose titles vary (Tech Lead, Lead Engineer, Architect).

#### Plain terms (portal CLIs)

```
payments engineering lead
checkout platform
fintech backend lead
LLM platform engineering
AI agents engineering lead
LLM evaluation
agentic AI
```

#### WebSearch-only (`site:` fallback)

```
site:linkedin.com/jobs "payments" ("Engineering Manager" OR "Staff Engineer") (India OR Germany OR Luxembourg)
site:jobs.ashbyhq.com ("LLM" OR "AI agents" OR "evaluation") ("Engineering Manager" OR "Staff Engineer")
site:wellfound.com "agentic" OR "LLM" "Engineering Manager" India
site:moovijob.com payments engineer Luxembourg
```

### Priority 4: Adjacent and Broader (wider net)

Adjacent roles and sectors: health-tech, gaming, travel-tech; AI evaluation program leadership.

#### Plain terms (portal CLIs)

```
Technical Program Manager AI
Engineering Lead
Tech Lead backend
Engineering Manager healthtech
Engineering Manager gaming
Backend Architect
```

#### WebSearch-only (`site:` fallback)

```
site:naukri.com ("Engineering Manager" OR "Tech Lead") (healthtech OR gaming OR travel) (Hyderabad OR Bangalore OR Gurgaon)
site:linkedin.com/jobs "Technical Program Manager" (LLM OR "AI evaluation")
site:linkedin.com/jobs "Engineering Lead" backend Remote India
```

## Location Filter

The candidate is based in Hyderabad and is open to remote, hybrid, onsite and relocation. Locations to pass to the CLIs and how to judge results:
- **Ideal:** Hyderabad (any work mode); Remote (India-based or global remote)
- **Acceptable:** Bangalore / Bengaluru, Pune, Gurgaon / Gurugram, Mumbai, any other Indian city (relocation OK); Germany (Berlin, Munich, Hamburg, Frankfurt) and Luxembourg **when visa sponsorship / relocation support is offered or not ruled out**
- **Borderline:** other countries abroad (flag for discussion); German or Luxembourg roles silent on sponsorship (proceed, mark unverified per the Eligibility Gate)
- **Too far / exclude:** roles abroad requiring EU citizenship or existing local work rights; roles requiring German, French or Luxembourgish as a job condition (Language Gate)

## Stack Filter

The candidate does not target Java roles. When assessing fit (Step 3), a posting whose core requirement is Java, Spring Boot, Kotlin or the JVM ecosystem is **Low** fit, even when the domain (payments, fintech) matches; name the Java requirement in its highlights. Postings that list Java only as a nice-to-have, or say Java experience is not required, are judged normally.

The candidate is backend-only (very basic React.js, no Android/iOS/frontend). Full-stack, frontend and mobile roles are **Low** fit. An EM/lead role whose team or core requirements are mainly frontend or mobile is **Low**; a mixed team where backend is the core stays as assessed, with the frontend/mobile part named in its highlights.

## Language Filter

Your working languages and levels are in CLAUDE.md's Languages table. When filtering scraped results, apply `04-job-evaluation.md`'s Language Gate: a posting requiring a language you haven't declared at all is excluded; a posting requiring a higher level than you declared in a language you do work in is not excluded, flag it clearly instead (see `job-scraper/SKILL.md`'s Step 3 "Quick Fit Assessment" for how the flag surfaces in `/scrape` output). Postings simply *written* in a language you don't work in, that don't require it on the job, are fine.

## Date Filter

Only include jobs posted within the last 14 days, or with an application deadline that has not yet passed. If a posting date cannot be determined, include it but flag as "date unknown".

## Adapting Queries

If the user specifies a focus area, select queries from the matching category and also generate 2-3 custom queries for that focus. For example:
- "/scrape [focus_area]" -> relevant category queries + custom focus-specific queries
