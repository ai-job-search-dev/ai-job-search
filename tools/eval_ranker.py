#!/usr/bin/env python3
"""Regression check for the ranker against postings you labelled by hand.

    python tools/eval_ranker.py --labels profile/ranker_labels.yaml            # keyword scorer only
    python tools/eval_ranker.py --labels profile/ranker_labels.yaml --llm      # with the configured model
    python tools/eval_ranker.py --labels profile/ranker_labels.yaml --llm --repeat 2

Labels file (job ids refer to your local job_search.db, so the file stays local):

    cases:
      - {id: 163, expect: shortlist, note: remote quantum internship}
      - {id: 31,  expect: veto,      note: cryogenics field work}
      - {id: 73,  expect: skip,      note: sales}
      - {id: 406, expect: maybe,     note: reported, never counted}

`shortlist` passes when the job is not vetoed and scores at or above your threshold, `veto` when it is vetoed,
`skip` when it is vetoed or below the threshold. Read-only: nothing is written to the database. With `--llm`
the answers are cached in a throwaway copy, so use `--repeat` to see how much the model varies between runs.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.db import resolve_db_path
from tools.llm import Budget, LLMGateway, llm_configured, preferences_hash
from tools.ranker import evaluate_with_instructor, heuristic_quantum_eval, keyword_in, load_config

EXPECTATIONS = {"shortlist", "veto", "skip", "maybe"}


def passes(expect: str, vetoed: bool, score: float, threshold: float) -> Optional[bool]:
    if expect == "maybe":
        return None
    if expect == "veto":
        return vetoed
    if expect == "skip":
        return vetoed or score < threshold
    return (not vetoed) and score >= threshold


def load_cases(path: Path) -> List[Dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = data.get("cases", [])
    bad = [c for c in cases if c.get("expect") not in EXPECTATIONS or "id" not in c]
    if bad:
        raise SystemExit(f"Invalid label entries (need id and expect in {sorted(EXPECTATIONS)}): {bad[:3]}")
    return cases


def run(labels: Path, use_llm: bool, repeat: int, db_path: Optional[Path], min_accuracy: float) -> int:
    prompts, prefs, candidate = load_config()
    threshold = prefs.shortlist_threshold
    source = resolve_db_path(db_path)
    scratch = Path(tempfile.mkdtemp(prefix="eval_ranker_")) / "scratch.db"
    shutil.copy(source, scratch)
    conn = sqlite3.connect(scratch)
    conn.row_factory = sqlite3.Row
    gateway = None
    if use_llm:
        if not llm_configured():
            raise SystemExit("--llm needs OPENAI_API_KEY (or a local server: tools/local_llm.sh env)")
        gateway = LLMGateway(conn, budget=Budget(None, None), use_cache=False, prefs_hash=preferences_hash(prefs))
    rows, started = [], time.time()
    for case in load_cases(labels):
        job = conn.execute(
            "SELECT j.id, j.title, j.location, j.is_remote, co.name AS company, "
            "COALESCE(NULLIF(j.cleaned_description, ''), j.raw_description, '') AS d "
            "FROM jobs j JOIN companies co ON co.id = j.company_id WHERE j.id = ?", (case["id"],)).fetchone()
        if job is None:
            rows.append({"id": case["id"], "title": "(not in database)", "expect": case["expect"], "ok": None, "scores": [], "vetoed": False})
            continue
        scores, vetoed, unsupported = [], False, 0
        for _ in range(repeat if use_llm else 1):
            if use_llm:
                ev = evaluate_with_instructor(job["title"], job["company"], job["d"], job["location"] or "", bool(job["is_remote"]),
                                              prompts["system_prompt"], prompts["user_prompt_template"], prefs=prefs,
                                              candidate=candidate, gateway=gateway, job_id=job["id"])
            else:
                ev = heuristic_quantum_eval(job["title"], job["company"], job["d"], job["location"] or "", bool(job["is_remote"]), prefs)
            scores.append(ev.overall_score)
            vetoed = vetoed or ev.should_veto
            text = job["d"].lower()
            unsupported += sum(1 for s in ev.matched_skills if not keyword_in(text, s.lower()) and s.lower() not in text)
        rows.append({"id": job["id"], "title": job["title"][:46], "expect": case["expect"], "vetoed": vetoed, "scores": scores,
                     "ok": passes(case["expect"], vetoed, min(scores) if case["expect"] == "shortlist" else max(scores), threshold),
                     "spread": round(max(scores) - min(scores), 1), "unsupported": unsupported})
        print(f"  evaluated {job['id']} ({len(rows)} done, {time.time() - started:.0f}s)", file=sys.stderr, flush=True)
    judged = [r for r in rows if r["ok"] is not None]
    correct = sum(1 for r in judged if r["ok"])
    print(f"Threshold {threshold}  |  scorer: {'model + keywords' if use_llm else 'keywords'}  |  repeat: {repeat if use_llm else 1}")
    print(f"{'id':>4}  {'expect':9} {'result':7} {'veto':5} {'scores':14} title")
    for r in rows:
        mark = {True: "ok", False: "FAIL", None: "-"}[r["ok"]]
        print(f"{r['id']:>4}  {r['expect']:9} {mark:7} {'V' if r['vetoed'] else '':5} {','.join(str(s) for s in r['scores']):14} {r['title']}")
    accuracy = correct / len(judged) if judged else 0.0
    print(f"\nAccuracy on {len(judged)} judged cases: {correct}/{len(judged)} = {accuracy:.0%}  ({time.time() - started:.0f}s)")
    if use_llm and repeat > 1:
        spreads = [r["spread"] for r in rows if r.get("spread") is not None]
        print(f"Run-to-run spread: max {max(spreads, default=0)}, mean {sum(spreads) / max(len(spreads), 1):.2f}")
    if use_llm:
        print(f"Skills claimed that the posting does not mention: {sum(r.get('unsupported', 0) for r in rows)}")
    return 0 if accuracy >= min_accuracy else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check the ranker against hand-labelled postings")
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--llm", action="store_true", help="Include the configured model (slow with a local one)")
    parser.add_argument("--repeat", type=int, default=1, help="Model runs per job, to measure variation")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--min-accuracy", type=float, default=0.8)
    args = parser.parse_args()
    sys.exit(run(args.labels, args.llm, max(1, args.repeat), args.db, args.min_accuracy))
