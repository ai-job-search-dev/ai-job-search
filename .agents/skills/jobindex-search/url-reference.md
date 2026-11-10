# Jobindex URL Reference

## Base URL

```
https://www.jobindex.dk/jobsoegning
```

## Full URL Pattern

```
https://www.jobindex.dk/jobsoegning/{category-group}/{category-slug}/{area-slug}?q={query}&jobage={days}&page={num}
```

All path segments and query params are optional. Category MUST come before area in the path.

## Path Segments

### Areas (geography)

Area slug goes at the end of the path:

| Area | Slug |
|------|------|
| Storkøbenhavn | `storkoebenhavn` |
| Københavnsområdet | `storkoebenhavn` |
| Nordsjælland | `nordsjaelland` |
| Sjælland | `sjaelland` |
| Fyn | `fyn` |
| Nordjylland | `nordjylland` |
| Midtjylland | `midtjylland` |
| Sydjylland | `sydjylland` |
| Bornholm | `bornholm` |

**Note:** If the exact slug is unknown, use the Geografi filter UI instead:
1. Click the "Geografi" button
2. Click "Tilføj område"
3. Type city/region name in the textbox
4. Select from autocomplete (treeitem)
5. Click "Vis X job" button
6. Note the URL path that results

### Categories

Category uses a two-part path: `{group}/{slug}`:

| Category | Path |
|----------|------|
| IT-drift og support | `it/itdrift` |

**Note:** Category slugs are not fully mapped. To discover a category slug:
1. Click the "Kategorier" button
2. Type category name in the search field
3. Select from autocomplete (treeitem)
4. Click "Vis X job" button
5. Note the URL path that results

## Query Parameters

| Parameter | Description | Examples |
|-----------|-------------|----------|
| `q` | Search query | `q=data+engineer` (broad), `q=%22data+engineer%22` (exact match) |
| `jobage` | Max age in days | `jobage=1` (today), `jobage=3`, `jobage=7`, `jobage=30` |
| `page` | Page number (1-indexed) | `page=1`, `page=2` |

## Examples

```bash
# Basic keyword search
playwright-cli goto "https://www.jobindex.dk/jobsoegning?q=python+developer"

# Exact match search (double quotes around query)
playwright-cli goto "https://www.jobindex.dk/jobsoegning?q=%22python+developer%22"

# Search in Storkøbenhavn area
playwright-cli goto "https://www.jobindex.dk/jobsoegning/storkoebenhavn?q=python+developer"

# Search with category + area
playwright-cli goto "https://www.jobindex.dk/jobsoegning/it/itdrift/storkoebenhavn?q=data+engineer"

# Last 7 days only
playwright-cli goto "https://www.jobindex.dk/jobsoegning?q=data+engineer&jobage=7"

# Page 2 of results
playwright-cli goto "https://www.jobindex.dk/jobsoegning?q=data+engineer&page=2"

# Everything combined
playwright-cli goto "https://www.jobindex.dk/jobsoegning/storkoebenhavn?q=data+engineer&jobage=7&page=1"
```

## Filters Available via UI Only

These filters require clicking through the filter panel (not URL-constructable):

**Ansættelsestype (Employment type):**
Fastansættelse, Tidsbegrænset, Studiejob, Graduate/trainee, Freelance, etc.

**Arbejdstid (Working hours):**
Fuldtid, Deltid

**Hjemmearbejde (Remote work):**
Muligt, Tilbydes ikke, 100% hjemmearbejde

To use these: click "Filtre" button → check desired options → click "Vis X job".

## Job Card Extraction

CSS selectors for job cards: `div.PaidJob, div.jix_robotjob`

Each card contains:
- Title: `h4 a` (text + href)
- Company: first `a` link text
- Location: div after h4 (clean by removing "Se rejsetid")
- Posted date: `time` element (format: DD-MM-YYYY)
- Description: `p` elements
- Job URL: `h4 a` href (may be external or jobindex-hosted)

~20 results per page.
