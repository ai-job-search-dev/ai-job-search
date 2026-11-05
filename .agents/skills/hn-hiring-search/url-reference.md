# Hacker News API reference

The endpoints and response shapes this skill depends on. Both APIs are public, keyless
and read-only. Update this file if either changes.

## Who is hiring? threads - hn.algolia.com

| Call | Purpose |
|------|---------|
| `GET https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=12` | Latest monthly threads posted by the `whoishiring` account. The CLI keeps hits whose `title` starts with `Ask HN: Who is hiring?` (the same account also posts "Who wants to be hired?" and "Freelancer?" threads). |
| `GET https://hn.algolia.com/api/v1/items/{id}` | One thread with its full comment tree. `children[]` are the top-level comments; each has `id`, `created_at_i` (unix seconds) and `text` (HTML). |

Each top-level comment is one employer's post. The community convention is a first
line of `Company | Role(s) | Location | ...`; the CLI parses it best-effort and keeps
the whole comment as `description`. Replies and meta comments without a pipe-separated
header are dropped.

## YC job posts - hacker-news.firebaseio.com (official HN API)

| Call | Purpose |
|------|---------|
| `GET https://hacker-news.firebaseio.com/v0/jobstories.json` | Up to 200 ids of current job posts (YC companies post these). |
| `GET https://hacker-news.firebaseio.com/v0/item/{id}.json` | One item: `title`, `url` (usually the company's careers page), `text` (HTML, optional), `time` (unix seconds), `dead`/`deleted`. |

`detail` uses the item endpoint for both sources: a who-is-hiring comment has `text`
and no `title`; a job story has a `title`. `isActive` is false when the item is
`dead` or `deleted`.

## Links

- Posting link: `https://news.ycombinator.com/item?id={id}`
- HN API docs: https://github.com/HackerNews/API
