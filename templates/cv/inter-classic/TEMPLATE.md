# Template: inter-classic

- **Type:** CV
- **Source extension:** .tex
- **Engine/toolchain:** lualatex
- **Page limit:** 2 page(s)
- **Fonts:** Latin Modern Sans (same font as the moderncv CVs) - ships with TeX Live; no bundled files. (Template was first built with Inter; switched at the user's request - the name is kept for continuity.)
- **Class/packages:** article (10pt, letterpaper); fontspec, geometry, xcolor, enumitem, titlesec, needspace, hyperref

## Compile command

    cd cv && rm -f <file>.pdf && mkdir -p build && lualatex -interaction=nonstopmode -output-directory=build <file>.tex && lualatex -interaction=nonstopmode -output-directory=build <file>.tex && mv build/<file>.pdf ./

## Style rules

- US Letter, margins 0.55in left/right, 0.45in top, 0.5in bottom; Latin Modern Sans 10pt body in black; moderncv blue accent `cvblue` = rgb(0.22,0.45,0.70) on the name, section headings and their rules, links, and bullet markers. Company names, titles, dates stay black.
- Header: name in UPPERCASE, regular weight 26pt, centred. Below the name, a bold 11pt target-role line (e.g. "Engineering Manager | Backend & Applied AI") so a recruiter sees the match at once. Contact line 8.5pt, centred, separated by ` | `, URLs written out in full and underlined (full URLs keep the text ATS-readable). 9pt line for work authorisation and relocation targets: Europe CVs keep it; India CVs omit it.
- Section headings: bold 11pt with a thin blue (cvblue!70, 0.5pt) rule underneath; sections are `\section*` (unnumbered).
- Section order: Summary, Work Experience, Skills, Education, Certifications or Courses, Languages. Core Competencies is optional and is omitted for EM CVs; EM leadership content goes into Skills as the first line (see Skills rule below).
- Confidentiality: never name clients or projects covered by an NDA. Use generic descriptors ("frontier AI labs", "enterprise clients") and the same wording in every CV, on LinkedIn and in interviews. Public benchmark names (SWE-bench, Tau-bench) are fine; client names (for example Amazon) are not.
- Certifications: only exam credentials go under "Certifications", with issuer, year and credential ID. Course-only entries go under "Courses". Leave out intro-level courses for senior CVs.
- Courses format: `Course title: Provider` (for example `Multi-Agent Systems with LangGraph; RAG with LangChain: DataCamp.com` or `Engineering Management and Leadership: Udemy.com`). Add a year only when known. EM CVs list management and leadership courses here; technical courses stay if they match the target role.
- Progression: do not add a "Progressed from X to Y" line; each role shows its own title and dates. Never state a promotion count unless the user has verified it.
- Core Competencies (optional, Staff and IC CVs only): 4-6 bullets, `\textbf{Area}`: short keyword phrases separated by semicolons, one or two lines each. Every item must be backed by an experience bullet, and metrics live in the experience section, not here.
- Skills (EM CVs): the last line, after the technical categories, is `\textbf{Leadership}`: people management, hiring & team scaling, performance management, roadmap delivery, stakeholder management.  Keep leadership terms as skill keywords; do not repeat headcount numbers here.
- Experience entry: `\company{Name}` bold on its own line, then `\role{Title}{Location}{Dates}` - bold-italic title left, italic location + ` · ` + bold dates right. Earlier roles at the same company use `\subrole{...}` with no repeated company line.
- Location format: `City, IN` (or `Remote`); dates `Mon YYYY - Mon YYYY` / `Mon YYYY - Present`.
- Bullets: round `$\bullet$` (the sans `\textbullet` renders as a square in Latin Modern Sans), tight (no item spacing), hanging indent; body text justified. Skills list is ragged-right with `\textbf{Category}:` labels.
- Education: `\edu{School}{Dates}{Degree}{Place}` - bold school/dates, italic degree/place.
- Summary: one paragraph, no bullets, 3-4 sentences (about 60-80 words), no first-person pronouns. Formula: title + years + speciality (backend, distributed services, AI/LLM products), then team size, then the biggest results with numbers (people results before business results for EM CVs), then the current role. Phrase the current role around scale and outcome ("Currently lead a team evaluating AI agents for frontier AI labs, improving review throughput by 30%"), never as "now lead X at Y" with no result, since that can read as a move away from building. No self-labels such as "hands-on" or "results-driven" without evidence; no number that is not in an experience bullet.
- Bullet count per role: the most recent role gets at most four bullets, with the engineering and tooling bullets first (for example a team-delivery bullet, two tooling bullets, then the trainer-team bullet). Cut anything beyond four rather than let the current role take the most space.
- Ordering within a role: lead with engineering or system work (pipelines, tooling, architecture, quality), then people leadership. Evaluator or trainer teams should be described by the systems they built and the quality or throughput they moved, not by the number of people. Say "members" rather than "engineers" for evaluator or trainer teams (for example "Led a 20-member team"), since they are not software engineers.
- Impact metrics: a bullet that claims a result should name what was measured (throughput, conversion, deploy frequency, availability). Keep an unverified metric out of the CV until the metric is known.
- The two oldest roles get at most two bullets each (final EM and Staff CVs use two each).
- Skills: list only technologies the candidate has actually used. Skills do not need a matching experience bullet; the Skills section is optional per CV, and the user decides which tools to show. Do not add a bullet just to back a skill.

## Known pitfalls

- `\clubpenalty`/`\widowpenalty` = 10000 stop a single bullet line being stranded on the next page.
- Hyphenation and double sentence spacing are disabled in the preamble (`\hyphenpenalty=10000`, `\frenchspacing`, `\sloppy`); keep them, or words split mid-line and summaries show wide gaps.
- Write `~` as `\textasciitilde` and escape `%`, `&`.
- `\company`/`\role` use `\needspace` so a heading never sits alone at a page bottom; this can leave a few blank lines at the bottom of page 1 - acceptable.
- No footer or page number (`\pagestyle{empty}`), and the text area runs to the 0.5in (36pt) bottom margin. Run the layout check as `python3 tools/verify_layout.py --footer-band 36 <file>.pdf`; the default 90pt band (calibrated for moderncv) reads every full page as a footer collision.
