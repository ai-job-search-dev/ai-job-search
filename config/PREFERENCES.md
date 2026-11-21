# Screening preferences

Every decision about which postings to keep, penalise or reject lives in one place: the `preferences:` block of `profile/master_profile.yaml`. The ranker (both the LLM and the keyword fallback), the shortlist threshold, the tailor and the JobSpy ingester all read it. Nothing about a person is hardcoded in `tools/` or in `config/prompts.yaml`.

Every key is optional. A profile with no `preferences` block uses the strict defaults shown below.

## How a rule works

Each screening rule has a mode.

| Mode      | Effect                                                          |
|-----------|-----------------------------------------------------------------|
| `veto`    | The posting is ignored and the reason is recorded               |
| `penalty` | The score drops by `penalty` points and the posting stays       |
| `off`     | The rule is ignored. The posting is scored as if it never fired |

```yaml
preferences:
  rules:
    research_phd:    { mode: "off" }
    non_engineering: { mode: "off" }
    management:      { mode: "penalty", penalty: 2.0 }
```

## Reference

| Key                           | Default                 | Meaning                                                                                   |
|-------------------------------|-------------------------|-------------------------------------------------------------------------------------------|
| `remote_only`                 | `true`                  | Keep only remote postings. Read by the JobSpy ingester                                    |
| `shortlist_threshold`         | `7.5`                   | Minimum overall score (0 to 10) to shortlist a posting                                    |
| `rules.hardware_lab`          | `veto`                  | Wet-lab, cryogenics, laser and cleanroom roles                                            |
| `rules.research_phd`          | `veto`                  | Research-only roles that need a PhD                                                       |
| `rules.non_engineering`       | `veto`                  | Sales, support, recruiting and operations                                                 |
| `rules.management`            | `veto`                  | Manager, director and head-of titles                                                      |
| `rules.citizenship_clearance` | `veto`                  | US citizenship or a security clearance. Skipped when `citizenships` includes `US`         |
| `rules.unpaid`                | `veto`                  | Unpaid or volunteer positions. Only applies when `employment.paid_only` is true           |
| `rules.work_authorization`    | `penalty`               | Postings tied to a region where you cannot work without a visa                            |
| `employment.accepted_types`   | all four                | `full_time`, `part_time`, `internship`, `contract`. A posting of any other type is vetoed |
| `employment.paid_only`        | `true`                  | Reject unpaid positions                                                                   |
| `employment.penalties`        | `{internship: 2.0}`     | Points subtracted per employment type. Set `internship: 0` to treat internships equally   |
| `seniority.senior_penalty`    | `1.0`                   | Points subtracted for senior and lead titles                                              |
| `seniority.staff_penalty`     | `2.0`                   | Points subtracted for staff, principal and distinguished titles                           |
| `scoring.domain_keywords`     | quantum computing terms | Terms that define your target field                                                       |
| `scoring.skill_keywords`      | common software skills  | Skills that count as a match. List only what you can claim                                |
| `scoring.domain_weight`       | `0.5`                   | Share of the score from field fit. `0` scores on engineering skills alone                 |
| `scoring.remote_bonus`        | `0.5`                   | Points added to remote postings                                                           |

## Work authorization

Remote postings are often tied to a country. Say where you can work and the ranker judges each posting against it.

```yaml
preferences:
  work_authorization:
    citizenships: ["IT"]
    current_country: "AR"
    authorized_regions: ["EU", "EEA", "AR"]        # no visa needed
    visa_possible_regions: ["US", "CA", "AU", "UK"] # you would apply for a visa
    visa_region_penalty: 1.0
    unauthorized_region_penalty: 2.0
    no_sponsorship_penalty: 2.0
```

Regions are ISO country codes plus the groups `EU` and `EEA`. A posting is tied to a region by its location (`Dublin, Ireland`) or by a clause such as "authorized to work in the United States". The penalty is:

| Posting                                                 | Penalty                       |
|---------------------------------------------------------|-------------------------------|
| Open to any country, or tied to an authorized region    | none                          |
| Tied to a `visa_possible_regions` entry                 | `visa_region_penalty`         |
| Tied to any other region                                | `unauthorized_region_penalty` |
| Employer refuses sponsorship and you are not authorized | `no_sponsorship_penalty`      |

The larger applicable penalty is used. Set `rules.work_authorization.mode: veto` to reject instead of penalising.

## Example profiles

A specialist who wants only field-relevant, engineering roles keeps the defaults.

A generalist who will take any paid engineering work:

```yaml
preferences:
  scoring:
    domain_weight: 0.0
    skill_keywords: ["python", "typescript", "react", "postgres", "docker"]
  rules:
    research_phd:    { mode: "veto" }
    non_engineering: { mode: "veto" }
  shortlist_threshold: 6.5
```

Someone open to anything paid, including internships and part-time work:

```yaml
preferences:
  employment:
    paid_only: true
    penalties: { internship: 0, part_time: 0, contract: 0 }
  rules:
    research_phd:    { mode: "off" }
    non_engineering: { mode: "off" }
```

## Adding a new rule

1. Add a `RuleSetting` field to `ScreeningRules` in `tools/profile_schema.py`.
2. Add its label and veto-reason pattern to `RULE_TEXT` in `tools/preferences.py`. The LLM prompt and the lifting of switched-off vetoes pick it up from there.
3. Call `apply(...)` for it in `heuristic_quantum_eval`, or add it to `deterministic_screen` if it needs no judgement.
4. Add a test to `tests/test_preferences.py`.
