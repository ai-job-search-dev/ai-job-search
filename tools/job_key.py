#!/usr/bin/env python3
"""Canonical dedup key for a job posting, and an audit for existing state.

`/scrape` Step 4 keys every seen_jobs.json entry by company+title. The rule was
prose only ("<url_or_company_title_key>"), so each run slugified in its own way
and the state file accumulated two distinct failures:

  * Keys carrying characters that break things downstream. `/apply` and
    `/outcome` derive an archive folder name from the same company+role pair,
    and documents/README.md's subfolder rule exists because a "/" splits that
    path across directories. Real examples found in a live workspace:
    "deloitte_junior-cybersecurity-analyst-(ot/iot)",
    "neverhack-estonia_penetration-tester-/-red-teamer",
    "ops-consulting,-llc_malware-analyst".

  * The same job stored twice under different keys, because one run truncated
    the title at a different point than the next. "deloitte_cyber-intelligence-
    center-security-analy" and "deloitte_cyber-intelligence-center-security-
    analyst-at" are one posting, one URL, two entries - and dedup is the whole
    point of the file.

Both are fixed by making the key a pure, deterministic function of the posting.
Truncation is length-capped *and* disambiguated by a hash of the full slug, so a
long title always produces the same key and two different long titles never
collide.

A title that slugifies to nothing (a posting written in a non-Latin script) has
no usable key half at all - "securion_" was a real entry, and it would have
collided with every future non-Latin posting from that company. Those fall back
to the portal's numeric id from the URL. A title or company that slugifies to
*less* than it says is the same problem one step on: "Программист 1С" and
"Аналитик 1С" both fold to "1", "Сбер AI" and "Яндекс AI" both fold to "ai",
so whenever the ASCII fold drops letters the surviving fragment is only a
readable prefix and the identity comes from the same fallback.

Usage:
  python3 tools/job_key.py --company "Acme Corp" --title "SOC Analyst (L2)"
  python3 tools/job_key.py --audit job_scraper/seen_jobs.json
  python3 tools/job_key.py --audit --migrate

`--audit --migrate` rewrites keys that no longer match the current rule, in
place: backup first, old->new reported, a new key that already exists is
reported and left alone, and a second run changes nothing.

Exit 0 when a key is produced, or when an audit finds nothing. Exit 1 when an
audit finds violations.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "job_scraper" / "seen_jobs.json"

COMPANY_MAX = 40
TITLE_MAX = 60
HASH_LEN = 6

# Anything outside this set becomes a separator. Deliberately strict: "/" and
# "," are the characters that actually caused damage, and an allowlist cannot
# be surprised by the next punctuation mark a job board invents.
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_JOB_ID = re.compile(r"(\d{6,})")


# Latin letters with no NFKD decomposition, which the ASCII fold would otherwise
# drop ("Ørsted" keyed as "rsted", "Mærsk" as "mrsk", "Kılıç" as "klc"). Values
# follow Unidecode 1.4.0; ICU's Latin-ASCII differs only on "ĸ" (q) and "ŋ" (n).
# "ĸ" and "ı" have no table capital: "ĸ" has none and "ı"'s is plain "I".
# "å" is deliberately absent: it decomposes to "a" plus a ring without losing a
# letter, and "å"->"aa" would move keys that work today for no gain (#537).
_LATIN_FOLD = {
    "ø": "o", "Ø": "O",
    "æ": "ae", "Æ": "AE",
    "œ": "oe", "Œ": "OE",
    "ß": "ss", "ẞ": "SS",
    "þ": "th", "Þ": "TH",
    "ð": "d", "Ð": "D",
    "đ": "d", "Đ": "D",
    "ł": "l", "Ł": "L",
    "ı": "i",
    "ħ": "h", "Ħ": "H",
    "ĸ": "k",
    "ŋ": "ng", "Ŋ": "NG",
    "ŧ": "t", "Ŧ": "T",
}
_LATIN_FOLD_TABLE = str.maketrans(_LATIN_FOLD)


def slugify(text: str) -> str:
    """Lowercase ASCII slug. Non-Latin scripts legitimately reduce to ''."""
    if not text:
        return ""
    # Fold after NFKD so a letter that decomposes into a fold-table letter plus
    # a mark ("ǿ" -> "ø" + acute) is folded too.
    decomposed = unicodedata.normalize("NFKD", str(text))
    folded = decomposed.translate(_LATIN_FOLD_TABLE)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    return _NON_SLUG.sub("-", ascii_only.lower()).strip("-")


def _lost_letters(text: str) -> bool:
    """True when the ASCII fold dropped non-Latin letters, so the slug under-identifies.

    NFKD turns "ü" into "u" plus a combining mark and "ﬁ" into "fi", so most
    Latin text keeps every letter. A Cyrillic, Greek, CJK or Arabic letter has
    no ASCII decomposition and vanishes; a mixed-script name then keeps only
    its Latin or digit fragment, and two different names can share it.

    Latin letters that also lack a decomposition ("ø", "æ", "ß", "ł") are
    not counted: slugify folds them through _LATIN_FOLD ("ø" -> "o"), so they
    keep their letter and are not lossy.
    """
    if not text:
        return False
    for ch in unicodedata.normalize("NFKD", str(text)):
        if ord(ch) < 128 or not ch.isalpha():
            continue
        if not unicodedata.name(ch, "LATIN").startswith("LATIN"):
            return True
    return False


def _cap(slug: str, limit: int) -> str:
    """Cap length without making truncation lossy across runs.

    A bare truncation is what produced the duplicate Deloitte entries: two runs
    cut the same title at different points and the file gained a second key for
    one job. Appending a hash of the *full* slug makes the result deterministic
    for a given title and distinct for any other.
    """
    if len(slug) <= limit:
        return slug
    digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:HASH_LEN]
    return f"{slug[:limit].rstrip('-')}-{digest}"


def make_key(company: str, title: str, url: str = "") -> str:
    """The canonical seen_jobs.json key for one posting."""
    company_slug = _cap(slugify(company), COMPANY_MAX)
    if not company_slug or _lost_letters(company):
        name = unicodedata.normalize("NFC", str(company or "").strip().casefold())
        # An absent name stays unknown; a non-Latin name still has an identity.
        # A mixed-script name keeps its Latin fragment as a readable prefix, but
        # the identity is the hash: "Сбер AI" and "Яндекс AI" both fold to "ai".
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:HASH_LEN]
        if not name:
            company_slug = "unknown-company"
        else:
            company_slug = f"{company_slug}-{digest}" if company_slug else f"company-{digest}"
    title_slug = _cap(slugify(title), TITLE_MAX)
    if not title_slug or _lost_letters(title):
        # No Latin characters in the title, or not enough of them to identify
        # it: "Программист 1С" and "Аналитик 1С" both fold to "1". The portal's
        # own numeric id is the stable handle; a surviving fragment stays as a
        # readable prefix only. Never emit a bare "company_" prefix.
        fragment = title_slug
        match = _JOB_ID.search(url or "")
        if match:
            title_slug = f"{fragment}-{match.group(1)}" if fragment else match.group(1)
        else:
            basis = slugify(unicodedata.normalize("NFKD", str(title or url or "")))
            # Hash the URL alone when there is one. The URL is the posting's
            # identity; the title is not. Including the title made the key
            # change whenever a portal re-listed the same posting with the
            # title altered, which stores one job twice - the failure this
            # whole helper exists to prevent. Portals whose ids carry no run
            # of six digits never reach the branch above, so for them this
            # hash is the only key half there is: freehire's real slugs look
            # like "inzhener-ooo-chen-hlk3qjfg", and its Cyrillic and Greek
            # titles slugify to nothing. With no URL, the title is all that
            # is left to key on.
            digest_basis = str(url) if url else str(title)
            digest = hashlib.sha1(digest_basis.encode("utf-8")).hexdigest()[:HASH_LEN]
            # A lossy fragment needs the digest to tell two postings apart; an
            # empty title keeps the bare basis (a URL-derived slug, or
            # "untitled-<hash>") exactly as before the fragment rule existed.
            title_slug = f"{fragment}-{digest}" if fragment else (basis or f"untitled-{digest}")
    return f"{company_slug}_{title_slug}"


# A canonical key is "<company-slug>_<title-slug>": lowercase alphanumerics and
# hyphens on either side of exactly one underscore. The underscore is the
# separator, so it is the one character outside the slug alphabet that belongs.
_CANONICAL = re.compile(r"^[a-z0-9][a-z0-9-]*_[a-z0-9][a-z0-9-]*$")


def is_canonical(key: str) -> bool:
    """Structurally safe as a dedup key and as an archive folder name."""
    return bool(key) and bool(_CANONICAL.match(key))


def is_legacy_shape(key: str) -> bool:
    """Old three-part "company_title_location" keys.

    Harmless - they carry no path-breaking character - but they are not what
    make_key produces, so a later run would store the same job under a new key
    and reintroduce a duplicate. Reported apart from real damage so the fix
    stays a decision rather than an automatic rename.
    """
    return bool(key) and key.count("_") > 1 and all(
        re.fullmatch(r"[a-z0-9][a-z0-9-]*", part) for part in key.split("_") if part
    )


def _load_seen(path: Path):
    """Return (document, seen) or print the reason and return None."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return None
    seen = doc.get("seen", doc) if isinstance(doc, dict) else doc
    if not isinstance(seen, dict):
        print(f"{path}: expected an object of job entries", file=sys.stderr)
        return None
    not_objects = [k for k, v in seen.items() if not isinstance(v, dict)]
    if not_objects:
        print(f"{path}: entry is not an object: {not_objects[0]}", file=sys.stderr)
        return None
    bad_url = [k for k, v in seen.items() if v.get("url") is not None and not isinstance(v["url"], str)]
    if bad_url:
        print(f"{path}: entry url is not a string: {bad_url[0]}", file=sys.stderr)
        return None
    return doc, seen


def _drifted(seen: dict) -> list[str]:
    """Canonical keys that differ from what make_key would produce today."""
    return [
        k for k, v in seen.items()
        if is_canonical(k) and k != make_key(v.get("company", ""), v.get("title", ""), v.get("url", ""))
    ]


def _rename_pass(seen: dict) -> tuple[dict[str, str], list[dict]]:
    """One pass: which drifted keys can move, and which are blocked.

    A key is blocked when its new key is held by an entry that does not itself
    move in this pass, or when an earlier entry (file order) claims the same
    new key. A blocked entry stays put, which can in turn block another, so
    the movers are narrowed until the set is stable. An entry whose new key is
    held by a mover is fine: the chain A -> x, B(on x) -> y moves together.
    """
    target = {
        k: make_key(seen[k].get("company", ""), seen[k].get("title", ""), seen[k].get("url", ""))
        for k in _drifted(seen)
    }
    movers = dict(target)
    blocked: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        claimed: set[str] = set()
        for old, new in list(movers.items()):
            if (new in seen and new not in movers) or new in claimed:
                blocked[old] = new
                del movers[old]
                changed = True
            else:
                claimed.add(new)
    return movers, [{"old": o, "new": n} for o, n in blocked.items()]


def migrate(path: Path, doc, seen: dict) -> dict:
    """Rewrite drifted keys in place; return the report.

    Runs passes until nothing renames. Deterministic (file order decides which
    of two entries claims a new key), never overwrites an existing key, and
    idempotent: once every rewritable key is current, a second run finds
    nothing to change and writes nothing. Collisions in the report are the
    final ones only.
    """
    current = dict(seen)
    migrated: list[dict] = []
    collisions: list[dict] = []
    for _ in range(len(seen) + 1):
        renames, collisions = _rename_pass(current)
        if not renames:
            break
        migrated.extend({"old": o, "new": n} for o, n in renames.items())
        current = {renames.get(k, k): v for k, v in current.items()}
    report = {
        "migrated": migrated,
        "migrate_collisions": collisions,
        "backup": None,
    }
    if not migrated:
        return report

    backup = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, backup)
    report["backup"] = str(backup)

    out = dict(doc, seen=current) if isinstance(doc, dict) and "seen" in doc else current
    # Same dump settings as tools/rank_state.save_state, so a migration changes
    # only the lines that carry a renamed key. Atomic replace: a half-written
    # seen_jobs.json loses the scrape history.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return report


def audit(path: Path, do_migrate: bool = False) -> int:
    loaded = _load_seen(path)
    if loaded is None:
        return 1
    doc, seen = loaded

    migration = {}
    if do_migrate:
        migration = migrate(path, doc, seen)
        if migration["migrated"]:
            loaded = _load_seen(path)
            if loaded is None:
                return 1
            doc, seen = loaded

    malformed = [k for k in seen if not is_canonical(k) and not is_legacy_shape(k)]
    legacy = [k for k in seen if is_legacy_shape(k)]
    by_url: dict[str, list[str]] = {}
    for key, entry in seen.items():
        url = (entry.get("url") or "").rstrip("/")
        if url:
            by_url.setdefault(url, []).append(key)
    duplicates = {u: ks for u, ks in by_url.items() if len(ks) > 1}
    # A key that does not match what make_key would produce today is drift, not
    # damage: reported separately so a rename is a choice (--migrate), never automatic.
    drift = _drifted(seen)

    print(json.dumps({
        "entries": len(seen),
        "malformed_keys": malformed,
        "legacy_three_part_keys": legacy,
        "duplicate_urls": duplicates,
        "keys_not_matching_current_rule": len(drift),
        **migration,
    }, indent=2, ensure_ascii=False))
    return 1 if (malformed or duplicates) else 0


def _force_utf8_output() -> None:
    """Write UTF-8 whatever the host's default encoding is.

    A piped stdout on Windows defaults to the ANSI code page (cp1252 on most
    Western installs), so printing a company, title or file name outside it
    raised UnicodeEncodeError before the workflow saw any output.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)  # absent on a StringIO under test
        if reconfigure:
            reconfigure(encoding="utf-8")


def main() -> int:
    _force_utf8_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--company")
    ap.add_argument("--title")
    ap.add_argument("--url", default="")
    ap.add_argument("--audit", nargs="?", const=str(STATE), metavar="STATE_JSON")
    ap.add_argument("--migrate", action="store_true", help="with --audit: rewrite drifted keys in place (backup first)")
    args = ap.parse_args()

    if args.migrate and not args.audit:
        ap.error("--migrate needs --audit")
    if args.audit:
        return audit(Path(args.audit), args.migrate)
    if args.company is None or args.title is None:
        ap.error("give --company and --title, or --audit")
    print(make_key(args.company, args.title, args.url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
