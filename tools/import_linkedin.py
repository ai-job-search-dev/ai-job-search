#!/usr/bin/env python3
"""Import a LinkedIn data export (zip, folder or single CSV) into the master profile.

Reads Profile.csv plus, when the export includes them, Positions.csv, Education.csv,
Skills.csv, Projects.csv, Certifications.csv and Languages.csv. Existing profile content
is kept; imported entries are merged in by identity (company+role, institution+degree,
skill name), so re-running is safe. The LinkedIn "Basic" export only contains Profile.csv;
the full "Want something in particular" export adds the rest.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.profile_manager import DEFAULT_PROFILE_FILE, load_profile, save_profile
from tools.profile_schema import (
    EducationEntry,
    LanguageSkill,
    ProjectEntry,
    SkillItem,
    WorkExperienceEntry,
)

CSV_NAMES = ("Profile", "Positions", "Education", "Skills", "Projects", "Certifications", "Languages")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def read_export(source: Path) -> Dict[str, List[Dict[str, str]]]:
    """Return {csv_name: rows} from a zip (nested zips allowed), a directory or one CSV."""
    tables: Dict[str, List[Dict[str, str]]] = {}

    def add(name: str, raw: bytes) -> None:
        stem = Path(name).stem
        if stem in CSV_NAMES and Path(name).suffix.lower() == ".csv":
            text = raw.decode("utf-8-sig", errors="replace")
            tables[stem] = list(csv.DictReader(io.StringIO(text)))

    def walk_zip(data: bytes) -> None:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                blob = zf.read(info)
                if info.filename.lower().endswith(".zip"):
                    walk_zip(blob)
                else:
                    add(info.filename, blob)

    if source.is_dir():
        for f in source.rglob("*.csv"):
            add(f.name, f.read_bytes())
    elif source.suffix.lower() == ".zip":
        walk_zip(source.read_bytes())
    else:
        add(source.name, source.read_bytes())
    return tables


def to_period(value: str) -> str:
    """LinkedIn dates ('Mar 2022', '2022') -> '2022-03' / '2022'; empty stays empty."""
    value = (value or "").strip()
    m = re.match(r"^([A-Za-z]{3})[a-z]*\s+(\d{4})$", value)
    if m and m.group(1).lower() in MONTHS:
        return f"{m.group(2)}-{MONTHS[m.group(1).lower()]:02d}"
    return value


def split_lines(text: str) -> List[str]:
    return [ln.strip(" -•\t") for ln in re.split(r"[\r\n]+", text or "") if ln.strip(" -•\t")]


def merge_profile(profile, tables: Dict[str, List[Dict[str, str]]]) -> List[str]:
    log: List[str] = []

    for row in tables.get("Profile", [])[:1]:
        name = f"{row.get('First Name', '').strip()} {row.get('Last Name', '').strip()}".strip()
        if name:
            profile.identity.name = name
        if row.get("Headline", "").strip():
            profile.identity.title = row["Headline"].strip()
        if row.get("Summary", "").strip():
            profile.summary = re.sub(r"\s+", " ", row["Summary"]).strip()
        if row.get("Geo Location", "").strip():
            profile.identity.location = row["Geo Location"].strip()
        log.append("profile: name, headline, summary")

    existing_jobs = {(e.company.casefold(), e.role.casefold()) for e in profile.experience}
    for row in tables.get("Positions", []):
        company, role = row.get("Company Name", "").strip(), row.get("Title", "").strip()
        if not company or not role or (company.casefold(), role.casefold()) in existing_jobs:
            continue
        end = to_period(row.get("Finished On", ""))
        profile.experience.append(WorkExperienceEntry(
            company=company, role=role, location=row.get("Location", "").strip(),
            start_date=to_period(row.get("Started On", "")) or "unknown",
            end_date=end or "Present", is_current=not end,
            achievements=split_lines(row.get("Description", "")),
        ))
        log.append(f"position: {role} @ {company}")

    existing_edu = {(e.institution.casefold(), e.degree.casefold()) for e in profile.education}
    for row in tables.get("Education", []):
        school = row.get("School Name", "").strip()
        degree = ", ".join(x for x in (row.get("Degree Name", "").strip(), row.get("Notes", "").strip()) if x)
        if not school or not degree or (school.casefold(), degree.casefold()) in existing_edu:
            continue
        period = " - ".join(x for x in (to_period(row.get("Start Date", "")), to_period(row.get("End Date", ""))) if x)
        profile.education.append(EducationEntry(degree=degree, institution=school, period=period))
        log.append(f"education: {degree} @ {school}")

    known_skills = {s.name.casefold() for s in profile.skills}
    for row in tables.get("Skills", []):
        name = row.get("Name", "").strip()
        if name and name.casefold() not in known_skills:
            profile.skills.append(SkillItem(name=name, category="Other", proficiency="Intermediate"))
            known_skills.add(name.casefold())
            log.append(f"skill: {name}")

    known_projects = {p.name.casefold() for p in profile.projects}
    for row in tables.get("Projects", []):
        title = row.get("Title", "").strip()
        if title and title.casefold() not in known_projects:
            profile.projects.append(ProjectEntry(
                name=title, description=re.sub(r"\s+", " ", row.get("Description", "")).strip() or title,
                repo_url=row.get("Url", "").strip() or None,
            ))
            log.append(f"project: {title}")

    for row in tables.get("Certifications", []):
        name = row.get("Name", "").strip()
        if name and name not in profile.certifications:
            profile.certifications.append(name)
            log.append(f"certification: {name}")

    known_langs = {lang.name.casefold() for lang in profile.identity.languages}
    for row in tables.get("Languages", []):
        name = row.get("Name", "").strip()
        if name and name.casefold() not in known_langs:
            profile.identity.languages.append(
                LanguageSkill(name=name, level=row.get("Proficiency", "").strip() or "Professional"))
            log.append(f"language: {name}")
    return log


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Merge a LinkedIn export into profile/master_profile.yaml")
    parser.add_argument("source", type=Path, help="LinkedIn export .zip, extracted folder, or a CSV")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_FILE)
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without saving")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.source.exists():
        print(f"error: {args.source} not found", file=sys.stderr)
        return 2
    tables = read_export(args.source)
    if not tables:
        print("error: no LinkedIn CSV files found in the source", file=sys.stderr)
        return 2
    missing = [n for n in ("Positions", "Education", "Skills") if n not in tables]
    profile = load_profile(args.profile)
    log = merge_profile(profile, tables)
    for line in log:
        print(f"  + {line}")
    if missing:
        print(f"note: export has no {', '.join(missing)}; add them with a full LinkedIn export or edit the profile by hand")
    if args.dry_run:
        print("dry-run: nothing saved")
    else:
        print(f"saved: {save_profile(profile, args.profile)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
