#!/usr/bin/env python3
"""Read-only probe of a live application form.

Opens a URL in a headless browser, lists the form fields and shows what `browser_apply.py`
would fill, leave to you, or upload. It never types, clicks, presses a key or submits.
The only change it makes is tagging each field with a `data-aja-idx` attribute so the
fields can be addressed, which does not alter the form's values or behaviour.

Usage:
  python tools/form_probe.py https://jobs.lever.co/acme/1234/apply
  python tools/form_probe.py URL --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.browser_apply import COLLECT_FIELDS_JS, application_url, detect_captcha, form_recognised, plan_fills
LOGIN_WORDS = re.compile(r"sign in|log in|login|create an account|password", re.IGNORECASE)


@dataclass
class ProbeResult:
    url: str
    final_url: str = ""
    title: str = ""
    field_count: int = 0
    form_found: bool = False
    needs_login: bool = False
    captcha: bool = False
    fields: List[Dict[str, Any]] = field(default_factory=list)
    would_fill: List[str] = field(default_factory=list)
    would_upload: List[str] = field(default_factory=list)
    left_for_you: List[str] = field(default_factory=list)
    error: Optional[str] = None


def analyse_fields(fields: List[Dict[str, Any]], answers: Dict[str, str]) -> Dict[str, List[str]]:
    """Classify collected fields the way browser_apply would, without a browser."""
    fills, left = plan_fills(fields, answers, "<cv.pdf>", "<cover-letter.pdf>")
    return {
        "would_fill": [f["key"] for f in fills if f["kind"] == "text"],
        "would_upload": [f["key"] for f in fills if f["kind"] == "file"],
        "left_for_you": left,
    }


def probe_form(url: str, headless: bool = True, timeout_ms: int = 25000, settle_ms: int = 2500) -> ProbeResult:
    from playwright.sync_api import sync_playwright

    result = ProbeResult(url=url)
    answers = {"first_name": "x", "last_name": "x", "full_name": "x", "email": "x", "phone": "x",
               "linkedin": "x", "github": "x", "portfolio": "x", "location": "x"}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=headless)
            try:
                page = browser.new_context().new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(settle_ms)
                result.final_url, result.title = page.url, page.title()
                fields = page.evaluate(COLLECT_FIELDS_JS)
                content = page.content()
            finally:
                browser.close()
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {str(exc)[:200]}"
        return result

    result.fields = fields
    result.field_count = len(fields)
    result.captcha = detect_captcha(content)
    result.needs_login = any(f.get("type") == "password" for f in fields)
    result.form_found = form_recognised(fields)
    result.__dict__.update(analyse_fields(fields, answers))
    return result


def render(result: ProbeResult) -> str:
    lines = [f"URL:        {result.url}"]
    if result.final_url and result.final_url != result.url:
        lines.append(f"Landed on:  {result.final_url}")
    if result.error:
        return "\n".join(lines + [f"Error:      {result.error}"])
    lines += [
        f"Title:      {result.title[:90]}",
        f"Fields:     {result.field_count} | form recognised: {result.form_found} | login wall: {result.needs_login} | captcha: {result.captcha}",
        f"Would fill: {', '.join(result.would_fill) or '-'}",
        f"Would upload: {', '.join(result.would_upload) or '-'}",
        f"Left to you ({len(result.left_for_you)}): {'; '.join(result.left_for_you[:10]) or '-'}",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only probe of an application form")
    parser.add_argument("url")
    parser.add_argument("--json", action="store_true", help="Print the full result as JSON")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument("--no-rewrite", action="store_true", help="Do not map posting URLs to their form URLs")
    parser.add_argument("--portal", default="", help="Source portal, e.g. greenhouse:ionq, to build the Greenhouse form URL")
    args = parser.parse_args(argv)
    url = args.url if args.no_rewrite else application_url(args.url, args.portal)
    result = probe_form(url, headless=not args.headed)
    print(json.dumps(asdict(result), indent=2) if args.json else render(result))
    return 1 if result.error else 0


if __name__ == "__main__":
    sys.exit(main())
