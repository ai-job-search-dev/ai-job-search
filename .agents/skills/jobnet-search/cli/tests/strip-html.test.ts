import { describe, expect, test } from "bun:test";
import { stripHtml } from "../src/helpers";

describe("stripHtml entity decoding", () => {
  test("decodes &amp; and the other basic entities", () => {
    expect(stripHtml("<p>R&amp;D &lt;3 &quot;x&quot;</p>")).toBe('R&D <3 "x"');
  });

  test("decodes an entity once: &amp;lt; is the text \"&lt;\", not \"<\"", () => {
    expect(stripHtml("<p>R&amp;lt;D &amp;amp; Ops</p>")).toBe("R&lt;D &amp; Ops");
  });

  // This parser has never decoded numeric references, so a numeric ampersand is
  // left alone rather than half-decoded; it must not become "&" either.
  test("a numeric ampersand is left literal, not turned into \"&\"", () => {
    expect(stripHtml("<p>&#38;amp;</p>")).toBe("&#38;amp;");
  });
});
