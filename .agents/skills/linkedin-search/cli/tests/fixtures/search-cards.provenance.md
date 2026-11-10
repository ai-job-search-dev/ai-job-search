# Offline search cards

`search-cards.json` stores the HTML string for the first two verbatim `<li>` cards from a real
LinkedIn guest search response captured by **portapulito** through the CLI's
`htmlFetch` on 2026-07-14 and published in [PR #153](https://github.com/ai-job-search-dev/ai-job-search/pull/153).
That PR was closed as an accidental personalized-fork submission, not merged.

Source: `.agents/skills/linkedin-search/cli/test/__fixtures__/search-list.html`
at commit `5bc3afb94d19b670ecf09287c20d0c95e5d13ba0`.
Original response SHA-256: `ff5fb9009f26c8f602b1ac4b018f471e76784300723b22db64d9d19c2b7f557c`.
Two-card excerpt SHA-256: `1ab95e6ccb38b50d922e4d714ff95bb58219c012489a3a94aca5e0adb7b552fb`.

The JSON string encoding preserves the original HTML whitespace without adding
trailing whitespace to source lines. The excerpt hash above is of the decoded HTML.

The valid-flag controls serve these saved cards over loopback HTTP. Their child
CLI preload accepts only the expected search endpoint and redirects that request
to loopback. Tests assert the actual URL parameters, successful output, IDs and
client-side result limits. No fixture regeneration or portal request occurs.
This isolates these controls; it is not an OS-level egress policy for the entire CI.
