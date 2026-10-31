# LinkedIn detail fixture

`detail-orbis-excerpt.html` contains verbatim excerpts from the guest response for
posting `4345841651`, captured by **portapulito** through this CLI's `htmlFetch`
on 2026-07-14 and published in [upstream PR #153](https://github.com/ai-job-search-dev/ai-job-search/pull/153).
That PR was closed because it was accidentally submitted to upstream instead of
the author's personalized fork; it was not merged.

Source: `.agents/skills/linkedin-search/cli/test/__fixtures__/detail-orbis.html`
at commit `5bc3afb94d19b670ecf09287c20d0c95e5d13ba0`.
Original SHA-256: `2125a346c22f471e3db08cd3890d0569b194703fe5f49b831cdde257e4aa4a59`.

Only the title, closed banner, description markup and criteria list are retained.
Their bytes are unmodified; recruiter cards, images, tracking links and unrelated
page sections are omitted. The description includes real adjacent paragraphs,
empty paragraphs and list items, rather than hand-written parser examples.

The CLI regression serves this saved response over loopback and redirects only
the exact expected guest endpoint to it in a child process. It makes no LinkedIn
requests and does not regenerate the fixture during tests.
