---
framework_version: 1.0.1
---

# Echo Traps

## Goal

A CV and cover letter should answer the posting in the candidate's own voice, not hand
the posting's words back. A recruiter who spots their own sentences, or their own
informal wording, in an application reads it as generated from the posting, and that
costs more than any keyword match gains. The goal is not to hide that AI helped; it is
text that reads as written from the candidate's career.

This file applies to CVs, cover letters, and application-form free text
(`08-application-forms.md`).

## Trap 1: Echo words

A posting has two kinds of vocabulary. Sort every posting term you are tempted to reuse
before you write it.

| Class | What it is | Rule |
|---|---|---|
| **Hard keywords** | Tools, methods, certifications, the job title, domain nouns: Kubernetes, MILP, IFRS 17, PRINCE2, RAG | Reuse exactly. ATS matching is often literal. |
| **Voice words** | Informal, idiomatic or metaphorical words, company coinages, and anything unusual for the field's register: "messy", "create leverage", "how things actually get done", "ship quickly", "startup-like" | Never reuse as the candidate's own words. Say the same thing in the field's normal register. |

**Spoken-word test:** would the candidate use this word unprompted when describing their
own work in an interview? If not, it does not go in the document.

**Example.** An operations-research posting asked for someone who "understands messy
operational and planning problems". The CV profile came back as "turns messy operational
and planning problems into ... tools". "Messy" is informal and rare in OR writing; the
interviewer later said it read as copied from the posting. In the field's register:
"ill-structured planning problems", "real-world operational and planning problems".

## Trap 2: Phrase lifts

Any run of **5 or more words** copied from the posting, outside quotation marks, is a
lift. Names, job titles and tool lists ("Python, SQL, dbt") do not count.
`tools/echo_check.py` finds the candidates (`LIFT` lines); judgment is only needed to drop
the ones that are just a keyword list. Its stock-phrase filter knows English function
words only, so in a draft in another language also drop hits that are a common phrase of
that language rather than wording distinctive to this posting. A lift reads as the posting pasted back, even when
every word is a hard keyword. Rewrite the sentence from the candidate's facts, keeping
the keywords.

Example: a posting says "translate the client's business needs into data solutions and
follow their delivery with the engineering team"; a letter that says "I translate the
client's business needs into data solutions" has lifted a clause. Say what the candidate
did instead (which client, which solution, what changed), and let the keywords fall into
that.

## Trap 3: Website echoes

The same as Trap 2 for the company's own pages: "About us" copy, mission statements,
recruiting slogans. Use researched facts (office cities, products, segments), not their
marketing sentences.

## Trap 4: Reuse across applications

The same letter sentence or paragraph sent to different companies. `tools/echo_check.py
--others "cover_letters/cover_*.tex"` reports these as `REUSE` lines. Shared facts are
fine; shared sentences are not. CV bullets are exempt: a master bullet reused across CVs
is normal, so CVs are not compared with each other.

## Not traps

- **Answering the posting's format openly.** "Against your 'what you bring' list:"
  followed by a point-by-point answer is what a careful human does. Quotation marks make
  it a reference to their text, not a claim to the words. Keep these short.
- Hard keywords and the exact job title, in headings or in prose.

## Flag table

The reviewer returns every hit in this table; the drafter carries it into the final report.

| # | Trap | Draft text | Posting / source text | Suggested rewrite |
|---|---|---|---|---|

Default handling in `/apply` Step 4: rewrite every hit, keeping the hard keywords, and
list original and rewrite in the Step 6 report so the user can revert any. Borderline
cases (a word that may be standard in the field) stay as they are and are listed as
"your call".
