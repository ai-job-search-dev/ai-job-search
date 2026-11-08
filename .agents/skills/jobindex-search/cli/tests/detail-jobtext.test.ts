import { describe, expect, test } from "bun:test";
import { parseDetailPage } from "../src/commands/detail";

// Jobindex redesigned its ad pages again after the 2026-08-19 rewrite: every
// ad fetched on 2026-11-08 (four h-ads and one r-ad) carries no `jd-*` block
// at all, only the `jobtext-jobad` layout below. Without a branch for it the
// page fell through to the external-ATS path, so company, location,
// employmentType and hours came back null and `description` was the visible
// text of the whole page - site navigation first, cookie-consent buttons last
// (10,142 chars for h1704469, about twice the ad). Trimmed from the live
// h1704469 page captured 2026-11-08; the contact-person card is left out.

const JOBTEXT_PAGE = `<!DOCTYPE html>
<html lang="da">
<head>
<title>Intern NAV/BC-udvikler – få indflydelse på ERP-platformen tæt på forretningen | Jobannonce | Jobindex</title>
<meta content="Intern NAV/BC-udvikler – få indflydelse på ERP-platformen tæt på forretningen" property="og:title">
<meta content="https://www.jobindex.dk/jobannonce/h1704469/intern-nav-bc-udvikler-faa-indflydelse-paa-erp-platformen-taet-paa-forretningen" property="og:url">
<meta content="Har du lyst til at komme tættere på brugerne og følge dine NAV/BC-løsninger hele vejen fra behov til drift?" property="og:description">
</head>
<body>
<header class="site-header" role="banner">
    <a href="#main-content" class="skip-to-main">Gå til sidens indhold</a>
    <div class="top-header"><span>37.000 job i dag</span> <a href="/">For jobsøgere</a> <a href="/arbejdsgiver">For arbejdsgivere</a> <a href="/login">Log ind</a></div>
</header>
<main id="main-content">
<div class="jobtext-jobad">
<div class="jobtext-jobad__main">
                <header class="jobtext-jobad__header">
                    <div>
                        <h1 class="h2">Intern NAV/BC-udvikler – få indflydelse på ERP-platformen tæt på forretningen</h1>
                            <div class="jobtext-jobad__metadata">
                                    <div class="jobtext-jobad__metadata-item">
                                        <svg aria-label="Virksomhed:" class="svg-icon svg-icon--xs"><use xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="#svg-company"></use></svg>
                                        Jobindex søger for GASA GROUP Denmark A/S
                                    </div>
                                    <div class="jobtext-jobad__metadata-item">
                                        <svg aria-label="Lokation:" class="svg-icon svg-icon--xs"><use xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="#svg-geolocation"></use></svg>
                                        Odense SV
                                    </div>
                            </div>
                    </div>
                </header>
                <section class="jobtext-jobad__body">
                    <h2 class="h3">Har du lyst til at komme tættere på brugerne og følge dine NAV/BC-løsninger hele vejen fra behov til drift?</h2>
<p>Som intern udvikler hos GASA GROUP arbejder du tæt sammen med kolleger inden for salg, indkøb, logistik og økonomi.</p>
<p>Hos os får du</p>
<ul>
<li>frihed til at planlægge dit arbejde, korte beslutningsveje og tid til at følge løsningerne ordentligt til dørs.</li>
<li>en varieret rolle med udvikling, integrationer, procesforbedringer og automatisering</li>
</ul>
                </section>
</div>
            <aside class="jobtext-jobad__sidebar">
<a class="button btn btn-primary   btn-block" href="/c?t=e6327777&amp;ctx=w&amp;source=jobtext" target="_blank">Ansøg</a>
                <div class="jobtext-jobad__sidebar-boxes">
<div class="vp-card d-print-none">
    <a class="vp-card__name" href="/virksomhed/1089/gasa-group-denmark-a-s#om-virksomhed">GASA GROUP Denmark A/S</a>
</div>
                        <div class="jobtext-sidebar-card">
                            <h3>Vigtige datoer</h3>
                            <dl>
                                <dt class="jobtext-sidebar-card__dt">Indrykket:</dt>
                                <dd class="jobtext-sidebar-card__dd"><time datetime="2026-11-05">5.&nbsp;november&nbsp;2026</time></dd>
                                <dt class="jobtext-sidebar-card__dt">Ansøgningsfrist:</dt>
                                <dd class="jobtext-sidebar-card__dd"><time datetime="2026-11-19">19.&nbsp;november&nbsp;2026</time></dd>
                            </dl>
                        </div>
                        <div class="jobtext-sidebar-card">
                            <h3>Ansættelse</h3>
                            <dl>
                                <dt class="jobtext-sidebar-card__dt">Ansættelsestype:</dt>
                                <dd class="jobtext-sidebar-card__dd">Fastansættelse</dd>
                                <dt class="jobtext-sidebar-card__dt">Hjemmearbejde:</dt>
                                <dd class="jobtext-sidebar-card__dd">Tilbydes ikke</dd>
                                <dt class="jobtext-sidebar-card__dt">Ugentlig arbejdstid:</dt>
                                <dd class="jobtext-sidebar-card__dd">Fuldtid</dd>
                            </dl>
                        </div>
                </div>
            </aside>
</div>
</main>
<div class="jix-cookie-consent-modal__buttons">
    <button id="jix-cookie-consent-accept-selected" type="button" class="btn btn-sm btn-secondary">
        <span class="jix-cookie-consent-accept-selected__necessary">Afvis</span>
        <span class="jix-cookie-consent-accept-selected__chosen" hidden>Accepter valgte</span>
    </button>
    <button id="jix-cookie-consent-accept-all" type="button" class="btn btn-sm btn-primary">Accepter alle</button>
</div>
</body>
</html>`;

// The r-ad shape: no company card in the sidebar (the header metadata item is
// the only company source), "Ansættelsesform" instead of "Ansættelsestype",
// and a deadline with no machine-readable date.
const JOBTEXT_PAGE_NO_CARD = JOBTEXT_PAGE
  .replace(/<div class="vp-card d-print-none">[\s\S]*?<\/div>\n/, "")
  .replace("Jobindex søger for GASA GROUP Denmark A/S", "TS STILLADSMONTAGE A/S")
  .replace("Ansættelsestype:", "Ansættelsesform:")
  .replace(/<dd class="jobtext-sidebar-card__dd"><time datetime="2026-11-19">[^<]*<\/time><\/dd>/, '<dd class="jobtext-sidebar-card__dd">Snarest muligt</dd>');

const URL = "https://www.jobindex.dk/jobannonce/h1704469";

describe("parseDetailPage - jobtext-jobad layout (live 2026-11-08)", () => {
  test("reads company, location, type and hours from the header and sidebar", () => {
    const d = parseDetailPage(JOBTEXT_PAGE, URL, "h1704469");
    expect(d.title).toBe("Intern NAV/BC-udvikler – få indflydelse på ERP-platformen tæt på forretningen");
    expect(d.company).toBe("GASA GROUP Denmark A/S");
    expect(d.location).toBe("Odense SV");
    expect(d.employmentType).toBe("Fastansættelse");
    expect(d.hours).toBe("Fuldtid");
    expect(d.date).toBe("2026-11-05");
    expect(d.deadline).toBe("2026-11-19");
    expect(d.applyUrl).toBe("https://www.jobindex.dk/c?t=e6327777&ctx=w&source=jobtext");
    expect(d.url).toBe(URL);
  });

  test("description is the ad body only, not the page chrome", () => {
    const d = parseDetailPage(JOBTEXT_PAGE, URL, "h1704469");
    expect(d.description).toStartWith("Har du lyst til at komme tættere på brugerne");
    expect(d.description).toContain("Hos os får du\nfrihed til at planlægge dit arbejde");
    expect(d.description).toEndWith("procesforbedringer og automatisering");
    for (const chrome of ["Gå til sidens indhold", "Log ind", "Accepter alle", "Afvis", "Fastansættelse"]) {
      expect(d.description).not.toContain(chrome);
    }
  });

  test("falls back to the header company when there is no company card", () => {
    const d = parseDetailPage(JOBTEXT_PAGE_NO_CARD, URL, "r14076266");
    expect(d.company).toBe("TS STILLADSMONTAGE A/S");
    expect(d.employmentType).toBe("Fastansættelse");
    expect(d.deadline).toBeNull();
  });
});
