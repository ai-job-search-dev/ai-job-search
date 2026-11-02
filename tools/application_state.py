#!/usr/bin/env python3
"""Application attempts: plan a draft, write its tracker row, resolve its archive.

list and plan are read-only. draft/update consume JSON from --input, use an
expected-row snapshot to reject stale edits, and atomically replace the CSV.
Archive contents remain owned by the canonical /apply and /outcome workflows.
"""

import argparse
import csv
import json
import math
import os
import sys
import tempfile
import uuid
from datetime import date
from pathlib import Path

from job_key import make_key

ROOT = Path(__file__).resolve().parent.parent
HEADER = "date,company,sector,role,role_type,channel,status,contact_person,fit_rating,notes,cv_file,cover_letter_file,source,deadline".split(",")
STATUSES = {"drafted", "applied", "interview", "offer", "hired", "rejected", "no_response", "offer_declined", "withdrawn"}
FINAL = {"hired", "rejected", "no_response", "offer_declined", "withdrawn"}


def status(value):
    return value.strip().casefold().replace(" ", "_")


def matches(row, company, role):
    return (row["company"].strip().casefold(), row["role"].strip().casefold()) == (company.strip().casefold(), role.strip().casefold())


def legacy_stem(row):
    text = f"{row['company']}_{row['role']}".lower().replace(" ", "_")
    return "_".join(filter(None, "".join(c for c in text if c.isalnum() or c == "_").split("_")))


def read_tracker(path):
    if not path.exists():
        return HEADER + ["application_id"], []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames
        if not fields or len(set(fields)) != len(fields) or not {"company", "role", "status"} <= set(fields):
            raise ValueError("tracker needs unique columns including company, role and status")
        rows = []
        ids = set()
        for row in reader:
            if None in row:
                raise ValueError("tracker has excess fields; repair the malformed CSV before writing")
            row = {k: v or "" for k, v in row.items()}
            ident = row.get("application_id", "")
            if ident and ident in ids:
                raise ValueError("tracker has duplicate application_id values")
            ids.add(ident)
            rows.append(row)
    return fields, rows


def archive(row, rows, root):
    ident = row.get("application_id")
    stem = ident or legacy_stem(row)
    if not stem or not all(c.isalnum() or c in "_-" for c in stem):
        raise ValueError("application identity must be one safe folder name")
    path = root / "documents" / "applications" / stem
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or path.resolve().parent != (root / "documents" / "applications").resolve():
        raise ValueError("application archive escapes documents/applications")
    # Multiple old rows share an archive; names cannot establish its owner.
    if not ident and sum((r.get("application_id") or legacy_stem(r)) == stem for r in rows) != 1:
        return None
    return str(path)


def describe(row, rows, root):
    path = archive(row, rows, root)
    return {"record": row, "archive": path, "warning": None if path else "Ambiguous legacy archive: select and verify this attempt's materials; never use the shared folder automatically."}


def plan(rows, company, role, ident, root):
    if not company.strip() or not role.strip():
        raise ValueError("company and role must be non-empty")
    selected = [r for r in rows if matches(r, company, role) and status(r["status"]) not in FINAL]
    if ident:
        selected = [r for r in selected if r.get("application_id") == ident]
        if not selected:
            raise ValueError("application_id does not identify an open matching attempt")
    if len(selected) > 1:
        raise ValueError("multiple open attempts; list them and select an application_id explicitly")
    if selected:
        row = selected[0].copy()
        path = archive(row, rows, root)
        if path is None:
            raise ValueError("ambiguous legacy archive; resolve its ownership before redrafting")
        return {"expected": row.copy(), "record": row, "archive": path, "stem": row.get("application_id") or legacy_stem(row)}
    row = {k: "" for k in HEADER}
    row.update(company=company, role=role, status="drafted", date=date.today().isoformat())
    row["application_id"] = make_key(company, role) + "__" + uuid.uuid4().hex
    return {"expected": None, "record": row, "archive": archive(row, rows, root), "stem": row["application_id"]}


def find_expected(rows, expected):
    if not isinstance(expected, dict):
        raise ValueError("update needs the exact expected record returned by list or plan")
    matches = [i for i, row in enumerate(rows) if row == expected]
    if len(matches) != 1:
        raise ValueError("tracker changed or selection is ambiguous; list/plan again before editing")
    return matches[0]


def mutate(command, payload, rows, root):
    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")
    expected = payload.get("expected")
    if command == "draft":
        row = payload.get("record")
        if not isinstance(row, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in row.items()):
            raise ValueError("record must contain string fields")
        row = row.copy()
        if not row.get("company", "").strip() or not row.get("role", "").strip():
            raise ValueError("record needs company and role")
        if row.get("fit_rating"):
            score = float(row["fit_rating"])
            if not math.isfinite(score) or not 0 <= score <= 100:
                raise ValueError("fit_rating must be a finite number between 0 and 100")
        if expected is None:
            ident = row.get("application_id", "")
            if not ident or any(r.get("application_id") == ident for r in rows):
                raise ValueError("new attempt needs an unused application_id from plan")
            prefix, separator, suffix = ident.rpartition("__")
            if not separator or prefix != make_key(row["company"], row["role"]) or uuid.UUID(hex=suffix).hex != suffix:
                raise ValueError("new attempt needs the application_id returned by plan")
            if any(matches(r, row["company"], row["role"]) and status(r["status"]) not in FINAL for r in rows):
                raise ValueError("an open attempt now exists; plan again")
            row["status"] = "drafted"
            row["date"] = date.today().isoformat()
            archive(row, rows, root)
            rows.append(row)
        else:
            index = find_expected(rows, expected)
            if any(row.get(k, "") != expected.get(k, "") for k in ("company", "role", "application_id")):
                raise ValueError("redrafting cannot change attempt identity")
            if status(expected["status"]) in FINAL:
                raise ValueError("closed attempts need a new draft plan")
            if archive(expected, rows, root) is None:
                raise ValueError("ambiguous legacy archive; resolve ownership before redrafting")
            updated = expected.copy()
            for key in ("cv_file", "cover_letter_file", "fit_rating", "source", "deadline"):
                if row.get(key) or key != "deadline":
                    updated[key] = row.get(key, "")
            updated["status"] = status(expected["status"])
            if updated["status"] == "drafted":
                updated["date"] = date.today().isoformat()
            updated["notes"] = append_note(updated.get("notes", ""), "redrafted")
            row = rows[index] = updated
    else:
        index = find_expected(rows, expected)
        changes = payload.get("changes", {})
        if not isinstance(changes, dict) or set(changes) - {"status", "date"} or not all(isinstance(v, str) for v in changes.values()):
            raise ValueError("changes may contain only string status and date fields")
        row = expected.copy()
        row.update(changes)
        row["status"] = status(row["status"])
        if row["status"] not in STATUSES:
            raise ValueError("unknown tracker status")
        if status(expected["status"]) == "drafted" and row["status"] != "drafted" and not changes.get("date"):
            raise ValueError("moving off drafted requires the actual submission date")
        if "date" in changes:
            date.fromisoformat(changes["date"])
        note = payload.get("note", "")
        if not isinstance(note, str):
            raise ValueError("note must be text")
        row["notes"] = append_note(row.get("notes", ""), note)
        separate = payload.get("separate_archive", False)
        if not isinstance(separate, bool):
            raise ValueError("separate_archive must be true or false")
        if separate:
            if expected.get("application_id"):
                raise ValueError("an existing application_id cannot be reassigned")
            row["application_id"] = make_key(row["company"], row["role"]) + "__" + uuid.uuid4().hex
        rows[index] = row
    return describe(row, rows, root)


def append_note(notes, note):
    return notes if not note or notes == note or notes.endswith("; " + note) else "; ".join(filter(None, (notes, note)))


def save_tracker(path, fields, rows):
    fields = fields + [k for k in HEADER + ["application_id"] if k not in fields]
    fields += [k for row in rows for k in row if k not in fields]
    fields = list(dict.fromkeys(fields))
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".applications.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)
    return fields


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("list", "plan", "draft", "update"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--company")
    parser.add_argument("--role")
    parser.add_argument("--application-id")
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    path = args.root / "job_search_tracker.csv"
    lock = path.with_suffix(".csv.lock")
    locked = False
    try:
        if args.command in ("draft", "update"):
            if args.input is None:
                raise ValueError("draft/update require --input JSON_FILE")
            # Serialize cooperating writers; a crashed process leaves a visible lock.
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            locked = True
        fields, rows = read_tracker(path)
        if args.command == "list":
            result = {"applications": [describe(r, rows, args.root) for r in rows]}
        elif args.command == "plan":
            if args.company is None or args.role is None:
                raise ValueError("plan requires --company and --role")
            result = plan(rows, args.company, args.role, args.application_id, args.root)
        else:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            result = mutate(args.command, payload, rows, args.root)
            fields = save_tracker(path, fields, rows)
            # Return the exact serialized snapshot, including empty extra columns.
            result["record"] = {k: result["record"].get(k, "") for k in fields}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, csv.Error) as exc:
        print(f"application_state: {exc}", file=sys.stderr)
        return 1
    finally:
        if locked:
            lock.unlink()


if __name__ == "__main__":
    sys.exit(main())
