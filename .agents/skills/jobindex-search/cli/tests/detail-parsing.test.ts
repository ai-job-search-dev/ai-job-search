import { describe, expect, test } from "bun:test";
import { parseDetailPage } from "../src/commands/detail";

// Jobindex redesigned its detail pages; every selector the old parser used
// (job-text, jix-info, jix_robotjob--area, jix-toolbar-top__company) is gone
// from live pages, so detail returned null company/location/date, CSS-comment
// text as the deadline, and an external ATS URL as its own id/url - exit 0,
// nothing signalling breakage (review finding F14, 2026-08-19, measured on
// 5/5 live postings). These fixtures are trimmed from live pages captured
// 2026-08-19: the jobindex-native "jd-*" shape and the external-ATS
// (hr-manager/Talentech) passthrough shape.

const NATIVE_PAGE = `<!DOCTYPE html>
<html lang="da">
<head>
  <title>VELLIV - Udvikler til Camunda/AWS</title>
  <meta property="og:title" content="Udvikler til Camunda/AWS" />
  <meta property="og:url" content="https://www.jobindex.dk/jobannonce/h1690934" />
  <meta property="og:description" content="VELLIV" />
</head>
<body>
  <div class="jd-appetizer">
    <h1>Udvikler til Camunda/AWS</h1>
  </div>
  <div id="container" class="container">
    <div class="row">
      <div class="twelve columns jd-details">
        <div class="jd-description">
          <p class="appetizer">En virksomhed med mere end 100 &aring;rs historie, der samtidig er cloud-only, er ikke hverdagskost.</p>
          <p>Hos Velliv f&aring;r du mulighed for at arbejde med Camunda, AWS og automatisering af processer.</p>
        </div>
      </div>
      <div class="four columns jd-facts">
        <div class="jd-type">
          <h3>Jobtype:</h3>
          <p>Fast</p>
        </div>
        <div class="jd-workhours">
          <h3>Arbejdstid:</h3>
          <p>Fuldtid</p>
        </div>
        <div class="jd-worktime">
          <h3>Arbejdsdage:</h3>
          <p>Dag</p>
        </div>
        <div class="jd-deadline">
          <h3>Ans&oslash;gningsfrist:</h3>
          <p>13. september 2026</p>
        </div>
        <div class="jd-location">
          <h3>Arbejdssted:</h3>
          <p>Ballerup</p>
        </div>
      </div>
    </div>
  </div>
</body>
</html>`;

// The external shape: an employer's ATS-hosted ad served through jobindex.
// og:url points at the ATS (NOT the posting), og:site_name is the ATS brand,
// and the only occurrence of the deadline label outside the widget is inside
// a CSS comment - the exact text the old regex captured as the deadline.
const EXTERNAL_PAGE = `<!DOCTYPE html>
<html>
<head>
  <title>
	Talentech - C#-udvikler til kritiske analysel&oslash;sninger i elnettet
</title>
  <meta name="description" content="Vi s&oslash;ger en udvikler til elnettet" />
  <meta itemprop="name" content="C#-udvikler til kritiske analysel&oslash;sninger i elnettet" />
  <meta property="og:title" content="C#-udvikler til kritiske analysel&oslash;sninger i elnettet" />
  <meta property="og:site_name" content="Talentech" />
  <meta property="og:url" content="https://candidate.hr-manager.net/ApplicationInit.aspx?cid=316&amp;ProjectId=188792" />
  <style>
    /* Defines the style of the Application Due text */
    /* DK: Ansøgningsfrist                           */
    /* BOKSTAV: K                                    */
    .frist { padding-bottom: 15px; }
  </style>
</head>
<body>
  <h1>C#-udvikler til kritiske analysel&oslash;sninger i elnettet</h1>
  <p>Vil du v&aelig;re med til at udvikle og drifte de systemer, der underst&oslash;tter udbygningen af Danmarks kommende elnet? Vi arbejder i krydsfeltet mellem IT og energi.</p>
  <div class="workplacelist emptyparent"><div id="workplacelist_lang" class="rowheader">Workplace</div><div class="widget-line"></div><span class="empty">Fredericia</span><br></div>
  <div class="frist emptyparent"><div id="frist_lang" class="rowheader">Application due</div><div class="widget-line"></div><span class="empty">21-09-2026</span><br></div>
</body>
</html>`;

const JOBANNONCE_URL = "https://www.jobindex.dk/jobannonce/";

describe("parseDetailPage - jobindex-native (jd-*) shape", () => {
  const job = parseDetailPage(NATIVE_PAGE, `${JOBANNONCE_URL}h1690934`, "h1690934");

  test("extracts the contract fields", () => {
    expect(job).toMatchObject({
      id: "h1690934",
      title: "Udvikler til Camunda/AWS",
      company: "VELLIV",
      location: "Ballerup",
      deadline: "2026-09-13",
      url: `${JOBANNONCE_URL}h1690934`,
    });
  });

  test("converts the Danish long date to ISO", () => {
    expect(job.deadline).toBe("2026-09-13");
  });

  test("extracts employment metadata and the description body", () => {
    expect(job.employmentType).toBe("Fast");
    expect(job.hours).toBe("Fuldtid");
    expect(job.description).toContain("Camunda, AWS og automatisering");
  });
});

describe("parseDetailPage - external ATS passthrough shape", () => {
  const job = parseDetailPage(EXTERNAL_PAGE, `${JOBANNONCE_URL}h1690445`, "h1690445");

  test("keeps the jobindex id and jobannonce URL, never the ATS og:url", () => {
    expect(job.id).toBe("h1690445");
    expect(job.url).toBe(`${JOBANNONCE_URL}h1690445`);
    expect(job.url).not.toContain("hr-manager.net");
  });

  test("extracts the title and never reports the ATS brand as the company", () => {
    expect(job.title).toBe("C#-udvikler til kritiske analyseløsninger i elnettet");
    expect(job.company).toBeNull();
  });

  test("extracts the workplace widget location", () => {
    expect(job.location).toBe("Fredericia");
  });

  test("finds the real deadline, not the CSS comment", () => {
    expect(job.deadline).toBe("2026-09-21");
  });

  test("description is readable body text, not a stylesheet", () => {
    expect(job.description).toContain("udbygningen af Danmarks kommende elnet");
    expect(job.description).not.toContain("padding-bottom");
  });

  test("deadline is null when only the CSS comment mentions the label", () => {
    const noDueWidget = EXTERNAL_PAGE.replace(
      /<div class="frist emptyparent">[\s\S]*?<br><\/div>/,
      "",
    );
    const parsed = parseDetailPage(noDueWidget, `${JOBANNONCE_URL}h9`, "h9");
    expect(parsed.deadline).toBeNull();
  });
});

// Browsers decode each character reference once, even when it creates another reference.
test("detail decodes escaped references only once", () => {
  const page = NATIVE_PAGE.replaceAll("Udvikler til Camunda/AWS", "R&amp;lt;D &#38;amp; &#x26;quot;");
  const job = parseDetailPage(page, `${JOBANNONCE_URL}h1690934`, "h1690934");
  expect(job.title).toBe("R&lt;D &amp; &quot;");
});

test("detail decodes a numeric ampersand one level: &#38;amp; is \"&amp;\"", () => {
  const page = NATIVE_PAGE.replaceAll("Udvikler til Camunda/AWS", "&#38;amp;");
  const job = parseDetailPage(page, `${JOBANNONCE_URL}h1690934`, "h1690934");
  expect(job.title).toBe("&amp;");
});

test("detail still decodes the Danish named references it carries", () => {
  const page = NATIVE_PAGE.replaceAll("Udvikler til Camunda/AWS", "S&oslash;ren &AElig;bl&aring;");
  const job = parseDetailPage(page, `${JOBANNONCE_URL}h1690934`, "h1690934");
  expect(job.title).toBe("Søren Æblå");
});
