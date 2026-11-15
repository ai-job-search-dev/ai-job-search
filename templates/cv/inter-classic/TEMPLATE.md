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
- Header: name in UPPERCASE, regular weight 26pt, centred. Contact line 8.5pt, centred, separated by ` | `, URLs written out in full and underlined (full URLs keep the text ATS-readable).
- Section headings: bold 11pt with a thin blue (cvblue!70, 0.5pt) rule underneath; sections are `\section*` (unnumbered).
- Section order: Summary, Core Competencies, Work Experience, Skills, Education, Certifications.
- Core Competencies: 4-6 bullets, `\textbf{Area}`: evidence and skills, each backed by a metric or fact from the experience section.
- Experience entry: `\company{Name}` bold on its own line, then `\role{Title}{Location}{Dates}` - bold-italic title left, italic location + ` · ` + bold dates right. Earlier roles at the same company use `\subrole{...}` with no repeated company line.
- Location format: `City, IN` (or `Remote`); dates `Mon YYYY - Mon YYYY` / `Mon YYYY - Present`.
- Bullets: round `$\bullet$` (the sans `\textbullet` renders as a square in Latin Modern Sans), tight (no item spacing), hanging indent; body text justified. Skills list is ragged-right with `\textbf{Category}:` labels.
- Education: `\edu{School}{Dates}{Degree}{Place}` - bold school/dates, italic degree/place.
- Summary: one paragraph, no bullets.

## Known pitfalls

- `\clubpenalty`/`\widowpenalty` = 10000 stop a single bullet line being stranded on the next page.
- Hyphenation and double sentence spacing are disabled in the preamble (`\hyphenpenalty=10000`, `\frenchspacing`, `\sloppy`); keep them, or words split mid-line and summaries show wide gaps.
- Write `~` as `\textasciitilde` and escape `%`, `&`.
- `\company`/`\role` use `\needspace` so a heading never sits alone at a page bottom; this can leave a few blank lines at the bottom of page 1 - acceptable.
- No footer or page number (`\pagestyle{empty}`), and the text area runs to the 0.5in (36pt) bottom margin. Run the layout check as `python3 tools/verify_layout.py --footer-band 36 <file>.pdf`; the default 90pt band (calibrated for moderncv) reads every full page as a footer collision.
