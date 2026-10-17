import { describe, expect, test } from "bun:test";
import { stripHtml } from "../src/helpers";

describe("stripHtml entity decoding", () => {
  test("decodes &amp; and the other basic entities", () => {
    expect(stripHtml("<p>R&amp;D &lt;3 &quot;x&quot;</p>")).toBe('R&D <3 "x"');
  });

  test("decodes an entity once: &amp;lt; is the text \"&lt;\", not \"<\"", () => {
    expect(stripHtml("<p>R&amp;lt;D &amp;amp; Ops</p>")).toBe("R&lt;D &amp; Ops");
  });
});
