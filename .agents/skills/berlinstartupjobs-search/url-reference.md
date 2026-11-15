# Berlin Startup Jobs - URL Reference

Recorded 2026-11-11 during `/add-portal` reconnaissance.

## Access

- `robots.txt`: `User-agent: *` disallows only `/wp-admin/` (allows `admin-ajax.php`). `/wp-json/` is permitted.
- No login or bot challenge on the REST API. User-Agent sent: `Mozilla/5.0 (compatible; berlinstartupjobs-cli/1.0)`.
- The site's own search box uses Algolia InstantSearch client-side; the CLI does not use Algolia.

## Endpoints

| Purpose | URL |
|---------|-----|
| Search / list | `GET https://berlinstartupjobs.com/wp-json/wp/v2/posts` |
| Detail by id | `GET https://berlinstartupjobs.com/wp-json/wp/v2/posts/<id>` |
| Detail by URL slug | `GET https://berlinstartupjobs.com/wp-json/wp/v2/posts?slug=<slug>` |
| Location terms | `GET .../wp-json/wp/v2/job_location` (Berlin, Germany = 28075; Remote = 28077; Remote Possible = 28076) |
| Categories | `GET .../wp-json/wp/v2/categories` (IT / Software Development = 9, slug `engineering`) |

## Parameters (posts)

| Param | Meaning |
|-------|---------|
| `search` | WordPress full-text search (title + content). Double-quoted phrase = exact phrase. |
| `after` | ISO datetime; posts published after it (used for `--jobage`). |
| `per_page` | Page size (CLI uses 20; WP max 100). |
| `page` | 1-indexed. Past the end -> HTTP 400 `rest_post_invalid_page_number`. |
| `orderby=date` | Newest first. |
| `_embed=wp:term` | Inline taxonomy terms (company, location, category, plan) in `_embedded["wp:term"]`. |
| `_fields` | Field whitelist; must include `_links,_embedded` for `_embed` to work. |
| `job_location=<id>` / `categories=<id>` | Taxonomy filters (not exposed as CLI flags). |

Response headers: `X-WP-Total` (match count), `X-WP-TotalPages`.

## Response structure (per post)

- `id` (number), `date` (local ISO), `link` (canonical posting URL)
- `title.rendered` - HTML-entity-encoded, format `"<Role> // <Company>"`
- `excerpt.rendered`, `content.rendered` - HTML
- `_embedded["wp:term"]` - array of arrays of `{ taxonomy, name, ... }`; taxonomies seen: `category`, `post_tag`, `job_company`, `job_location`, `job_plan`
