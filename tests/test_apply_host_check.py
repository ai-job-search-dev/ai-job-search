"""Guards for /apply's source host verification rule in Step 1 (#431).

Pins the invariants from the maintainer design in issue #431:
- URLs must be verified before drafting against installed portal boards or known ATS apexes.
- The 6 standard ATS apex domains must be checked: greenhouse.io, lever.co,
  myworkdayjobs.com (or workday.com), ashbyhq.com, smartrecruiters.com, workable.com.
- Look-alike attacks (prefixes, suffixes, userinfo tricks) must fail closed.
- Any other host must be named plainly in the output as unverified.

The installed-portal half is derived, not copied (#497). Rule 1 in apply.md
defines an installed portal as "any configured job portal in `.agents/skills/`",
so the source of truth is the skills tree: the host literals in each
`*-search/cli/src/**/*.ts`. A hand-copied set in this file went stale the moment
a portal was added or dropped, and a new portal CLI fetching from a host the
copy lacked had its postings classified "unverified" with no test failing.
The ATS apex list stays the fixed set it is.
"""

import re
import unittest
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
APPLY_COMMAND_FILE = REPO / ".claude" / "commands" / "apply.md"
SKILLS_DIR = REPO / ".agents" / "skills"

# A host literal in a CLI source file. Portal CLIs build every request URL from
# a BASE_URL / SEARCH_URL constant, so this is where the board's host lives.
HOST_LITERAL = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# Hosts a source file may name that are not the portal: schema vocabularies,
# RFC example domains, loopback. Extend if a CLI ever embeds another one.
NON_PORTAL_HOSTS = {"schema.org", "www.w3.org", "example.com", "localhost"}


def installed_portal_skills() -> list[Path]:
    """Every `*-search` skill with a CLI source tree, enabled or not.

    Rule 1 says "configured job portal in `.agents/skills/`" - a portal that is
    installed but `enabled: false` for /scrape is still a legitimate board for
    a posting the user pastes into /apply, so discovery is by installation.
    """
    return sorted(
        d for d in SKILLS_DIR.glob("*-search") if (d / "cli" / "src").is_dir()
    )


def portal_enabled(skill_dir: Path) -> bool:
    """The `enabled:` frontmatter flag /scrape honours; a missing key means enabled."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return True
    match = re.search(r"^enabled:\s*(true|false)\b", skill_md.read_text(encoding="utf-8"), re.M)
    return match is None or match.group(1) == "true"


def portal_hosts(skill_dir: Path) -> set[str]:
    """The board hosts a portal CLI fetches from, as registrable names.

    A leading `www.` is dropped so the classifier's exact-or-subdomain rule
    accepts both `jobindex.dk` and `www.jobindex.dk` (the CLI builds its URLs
    with the latter; postings link to both).
    """
    hosts: set[str] = set()
    for source in (skill_dir / "cli" / "src").rglob("*.ts"):
        for host in HOST_LITERAL.findall(source.read_text(encoding="utf-8")):
            host = host.lower()
            if host in NON_PORTAL_HOSTS:
                continue
            hosts.add(host[4:] if host.startswith("www.") else host)
    return hosts


def discover_installed_portal_hosts() -> set[str]:
    return set().union(*(portal_hosts(d) for d in installed_portal_skills()))


INSTALLED_PORTAL_HOSTS = discover_installed_portal_hosts()

KNOWN_ATS_APEXES = {
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "workday.com",
    "ashbyhq.com",
    "smartrecruiters.com",
    "workable.com",
}



def resolve_portal_skill(url_str: str) -> Path | None:
    """Resolve a posting URL to its matching installed portal skill directory, if any."""
    try:
        parsed = urlparse(url_str)
        host = (parsed.hostname or "").lower().strip()
    except Exception:
        return None
    if not host:
        return None
    for skill in installed_portal_skills():
        for portal in portal_hosts(skill):
            if host == portal or host.endswith(f".{portal}"):
                return skill
    return None


def classify_posting_host(url_str: str, installed_portals: set[str] | None = None) -> tuple[str, str]:
    """Reference implementation of the host provenance rule in /apply Step 1.

    `installed_portals` defaults to the set discovered from the skills tree, so
    the classifier can never lag a portal that /add-portal installed.

    Returns (tier, host), where tier is one of:
      - 'installed_portal'
      - 'official_ats'
      - 'unverified'
    """
    if installed_portals is None:
        installed_portals = INSTALLED_PORTAL_HOSTS
    try:
        parsed = urlparse(url_str)
        host = (parsed.hostname or "").lower().strip()
    except Exception:
        return "unverified", ""

    if not host:
        return "unverified", ""

    # Check installed portal boards (exact match or subdomain match)
    for portal in installed_portals:
        if host == portal or host.endswith(f".{portal}"):
            return "installed_portal", host

    # Check known official ATS apexes (exact match or subdomain match)
    for apex in KNOWN_ATS_APEXES:
        if host == apex or host.endswith(f".{apex}"):
            return "official_ats", host

    return "unverified", host


class ApplyPostingGateWiringTests(unittest.TestCase):
    """The posting passes tools/posting_guard.py before Step 1 evaluates it, the
    component that fetches it has no Edit/Write, and the reviewer returns its
    research as text for the orchestrator to write."""

    def setUp(self):
        self.text = APPLY_COMMAND_FILE.read_text(encoding="utf-8")
        self.step0 = re.search(r"## Step 0: Parse Input(.*?)(?=## Step 1:)", self.text, re.DOTALL).group(1)
        self.step1 = re.search(r"## Step 1: DRAFTER - Evaluate Fit(.*?)(?=## Step 2:)", self.text, re.DOTALL).group(1)
        self.step3 = re.search(r"## Step 3: REVIEWER - Research & Critique(.*?)(?=## Step 4:)", self.text, re.DOTALL).group(1)
        self.step4 = re.search(r"## Step 4: DRAFTER - Revise Based on Feedback(.*?)(?=## Step 5:)", self.text, re.DOTALL).group(1)

    def test_step0_dispatches_posting_fetcher_and_runs_guard(self):
        self.assertIn("posting-fetcher", self.step0, "a URL posting is fetched by the least-privilege agent")
        self.assertIn("posting_guard", self.step0, "Step 0 must name the integrity gate")
        self.assertIn("--text", self.step0, "pasted text is gated too, in text mode")
        self.assertIn("Portal CLI first", self.step0)  # the escalation order is unchanged

    def test_step1_prints_posting_integrity_line(self):
        self.assertIn("Posting integrity", self.step1)
        self.assertIn("Continue with this posting?", self.step1)
        self.assertLess(
            self.step1.index("Posting integrity"), self.step1.index("Source host verification"),
            "the integrity verdict is the first line of the evaluation",
        )

    def test_step3_dispatches_application_reviewer_and_orchestrator_writes_cache(self):
        self.assertIn("application-reviewer", self.step3)
        self.assertNotIn("general-purpose", self.step3)
        self.assertIn("### Company research (for the cache)", self.step3,
                      "the reviewer returns the cache as text - it has no Write tool")
        self.assertIn("company_research/", self.step4, "the orchestrator writes the cache after the reviewer returns")


class ApplyHostVerificationSpecTests(unittest.TestCase):
    def setUp(self):
        self.text = APPLY_COMMAND_FILE.read_text(encoding="utf-8")
        step0_match = re.search(r"## Step 0: Parse Input(.*?)(?=## Step 1:)", self.text, re.DOTALL)
        self.assertTrue(step0_match, "Step 0 must exist in apply.md")
        self.step0_text = step0_match.group(1)
        step1_match = re.search(r"## Step 1: DRAFTER - Evaluate Fit(.*?)(?=## Step 2:)", self.text, re.DOTALL)
        self.assertTrue(step1_match, "Step 1 must exist in apply.md")
        self.step1_text = step1_match.group(1)

    def test_step0_documents_portal_cli_detail_before_webfetch(self):
        self.assertIn("Portal CLI first", self.step0_text)
        self.assertIn("detail", self.step0_text)
        self.assertIn("WebFetch fallback", self.step0_text)
        cli_pos = self.step0_text.find("Portal CLI first")
        webfetch_pos = self.step0_text.find("WebFetch fallback")
        self.assertNotEqual(cli_pos, -1)
        self.assertNotEqual(webfetch_pos, -1)
        self.assertLess(cli_pos, webfetch_pos, "Portal CLI detail must precede WebFetch in apply.md Step 0")

    def test_step1_contains_source_host_verification_heading(self):
        self.assertIn("Source Host Verification", self.step1_text)

    def test_step1_documents_all_six_ats_apexes(self):
        for apex in ["greenhouse.io", "lever.co", "myworkdayjobs.com", "ashbyhq.com", "smartrecruiters.com", "workable.com"]:
            self.assertIn(apex, self.step1_text, f"Step 1 must specify ATS apex: {apex}")

    def test_step1_documents_look_alike_fail_closed_rules(self):
        self.assertIn("evil-greenhouse.io", self.step1_text)
        self.assertIn("fail closed", self.step1_text)

    def test_step1_requires_unverified_hosts_to_be_named_plainly(self):
        self.assertIn("Unverified source host", self.step1_text)

    def test_classifier_identifies_official_ats_subdomains(self):
        urls = [
            "https://boards.greenhouse.io/acme/jobs/12345",
            "https://job-boards.greenhouse.io/acme/jobs/12345",
            "https://jobs.lever.co/corp/67890",
            "https://acme.myworkdayjobs.com/en-US/Careers/job/1",
            "https://jobs.ashbyhq.com/startup/abc-123",
            "https://jobs.smartrecruiters.com/Enterprise/456",
            "https://apply.workable.com/tech-corp/j/789/",
        ]
        for url in urls:
            tier, host = classify_posting_host(url)
            self.assertEqual(tier, "official_ats", f"{url} should classify as official_ats, got {tier}")

    def test_classifier_identifies_installed_portal_hosts(self):
        # A regional subdomain and the bare apex must both pass for every portal;
        # the derived set is what makes this hold for a portal added by /add-portal.
        urls = [
            "https://www.jobindex.dk/jobannonce/12345",
            "https://dk.linkedin.com/jobs/view/999999",
            "https://jobnet.dk/find-job/8888",
            "https://freehire.me/jobs/golang-zensar-2bxu6dxm",
        ]
        for url in urls:
            tier, host = classify_posting_host(url)
            self.assertEqual(tier, "installed_portal", f"{url} should classify as installed_portal, got {tier}")

    def test_classifier_default_is_the_discovered_set_not_a_copy(self):
        self.assertEqual(classify_posting_host.__defaults__, (None,))
        self.assertTrue(INSTALLED_PORTAL_HOSTS, "discovery found no portal hosts under .agents/skills/")
        # An explicit set still overrides, which is how a fork's test can probe a candidate portal.
        self.assertEqual(
            classify_posting_host("https://jobs.newboard.test/x", {"newboard.test"})[0], "installed_portal"
        )
        self.assertEqual(classify_posting_host("https://jobs.newboard.test/x")[0], "unverified")


class InstalledPortalsAreVerifiedTests(unittest.TestCase):
    """Every installed portal CLI's board host must classify as installed_portal.

    This is the assertion the hand-copied set could not make: a portal added
    by /add-portal fetches from a host the copy never heard of, and its
    postings reached /apply flagged "unverified" while the suite stayed green.
    """

    def test_every_installed_portal_has_a_discoverable_host(self):
        skills = installed_portal_skills()
        self.assertTrue(skills, "no *-search skill with a cli/src tree under .agents/skills/")
        for skill in skills:
            with self.subTest(portal=skill.name):
                self.assertTrue(
                    portal_hosts(skill),
                    f"{skill.name}/cli/src has no host literal - the classifier cannot verify its postings",
                )

    def test_every_installed_portals_host_classifies_as_installed_portal(self):
        for skill in installed_portal_skills():
            for host in sorted(portal_hosts(skill)):
                for url in (f"https://{host}/job/1", f"https://www.{host}/job/1", f"https://sub.{host}/job/1"):
                    with self.subTest(portal=skill.name, url=url, enabled=portal_enabled(skill)):
                        tier, _ = classify_posting_host(url)
                        self.assertEqual(tier, "installed_portal", f"{url} classified as {tier}")

    def test_portal_hosts_are_registrable_names_without_www(self):
        for skill in installed_portal_skills():
            for host in portal_hosts(skill):
                with self.subTest(portal=skill.name, host=host):
                    self.assertFalse(host.startswith("www."), host)
                    self.assertRegex(host, r"^[a-z0-9.-]+\.[a-z]{2,}$")

    def test_enabled_flag_reads_like_scrape(self):
        # /scrape: "a missing key means enabled". Both shipped states occur in the tree.
        states = {skill.name: portal_enabled(skill) for skill in installed_portal_skills()}
        self.assertTrue(any(states.values()), f"no enabled portal among {states}")
        self.assertIsInstance(portal_enabled(SKILLS_DIR / "does-not-exist-search"), bool)

    def test_classifier_fails_closed_on_look_alikes_and_unverified_hosts(self):
        suspicious = [
            "https://evil-greenhouse.io/job/1",
            "https://boards.greenhouse.io.evil.com/job/1",
            "https://boards.greenhouse.io@evil-domain.com/job/1",
            "https://myworkdayjobs.com.phishing.net/login",
            "https://lever.co.attacker.org/apply",
            "https://unknown-board.example.com/posting/123",
            # Anyone can publish a "job posting" in a GitHub repo or gist. A
            # contact URL in a portal CLI's User-Agent string once made
            # github.com an installed-portal host (#633), so pin it here.
            "https://github.com/acme-careers/jobs/blob/main/README.md",
            "https://gist.github.com/someone/0123456789abcdef",
        ]
        for url in suspicious:
            tier, host = classify_posting_host(url)
            self.assertEqual(tier, "unverified", f"{url} must fail closed as unverified, got {tier}")

    def test_resolve_portal_skill_maps_all_installed_portal_hosts(self):
        """Every installed portal skill must resolve URLs for each host it queries (#535)."""
        skills = installed_portal_skills()
        self.assertGreater(len(skills), 0, "must discover installed portal skills")
        for skill in skills:
            hosts = portal_hosts(skill)
            self.assertGreater(len(hosts), 0, f"{skill.name} must declare at least one host")
            for host in hosts:
                for test_url in (f"https://{host}/posting/123", f"https://sub.{host}/posting/123"):
                    with self.subTest(skill=skill.name, host=host, url=test_url):
                        resolved = resolve_portal_skill(test_url)
                        self.assertIsNotNone(resolved, f"failed to resolve skill for {test_url}")
                        self.assertEqual(resolved.name, skill.name)
                        self.assertTrue((resolved / "cli" / "src" / "cli.ts").is_file())

    def test_resolve_portal_skill_returns_none_for_non_portal_urls(self):
        unrelated = [
            "https://example.com/posting/123",
            "https://greenhouse.io/corp/123",
            "not-a-valid-url",
        ]
        for url in unrelated:
            with self.subTest(url=url):
                self.assertIsNone(resolve_portal_skill(url))


class WebResearchEscalationOrderTests(unittest.TestCase):
    def setUp(self):
        self.research_file = REPO / ".claude" / "skills" / "job-application-assistant" / "09-web-research.md"
        self.text = self.research_file.read_text(encoding="utf-8")
        match = re.search(r"## Escalation order(.*?)(?=## |\Z)", self.text, re.DOTALL)
        self.assertTrue(match, "Escalation order section must exist in 09-web-research.md")
        self.order_text = match.group(1)

    def test_escalation_order_lists_portal_cli_first(self):
        self.assertIn("Installed portal CLI `detail` command", self.order_text)
        self.assertIn("WebFetch", self.order_text)
        portal_pos = self.order_text.find("Installed portal CLI `detail` command")
        webfetch_pos = self.order_text.find("`WebFetch`")
        self.assertNotEqual(portal_pos, -1)
        self.assertNotEqual(webfetch_pos, -1)
        self.assertLess(portal_pos, webfetch_pos, "Portal CLI detail must precede WebFetch in escalation order")


class RankBatchFetchSpecTests(unittest.TestCase):
    def setUp(self):
        self.rank_file = REPO / ".claude" / "commands" / "rank.md"
        self.text = self.rank_file.read_text(encoding="utf-8")
        match = re.search(r"## Step 2: Batch-Fetch and Score(.*?)(?=## Step 3:)", self.text, re.DOTALL)
        self.assertTrue(match, "Step 2 must exist in rank.md")
        self.step2_text = match.group(1)

    def test_rank_step2_documents_portal_cli_detail_fetch(self):
        self.assertIn("detail", self.step2_text)
        self.assertIn("09-web-research.md", self.step2_text)


if __name__ == "__main__":
    unittest.main()

