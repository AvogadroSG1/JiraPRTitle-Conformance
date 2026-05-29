# StackEng Jira Ticket Compliance Scanner

Scans merged PRs across all StackEng GitHub repos and evaluates whether they reference a Jira ticket in the title or body. Produces per-repo and org-wide compliance reports.

## Prerequisites

- Python 3.11+
- `gh` CLI authenticated with access to the StackEng org (`gh auth status`)
- No pip dependencies — stdlib only

## Quick Start

```bash
cd ~/peter_code/scratch_work/pr_jira_analysis_code

# 1. Fetch PRs (default: all StackEng repos, last 6 months)
python fetch_prs.py --verbose

# 2. Analyze for Jira compliance
python analyze_jira.py --verbose

# 3. Generate report
python report.py --format both
```

Output lands in `output/report.md` (human-readable) and `output/report.json` (machine-readable).

## Ticket Detection

The scanner looks for Jira ticket references in both PR **title** and **body**:

| Format | Example | Detected? |
|--------|---------|-----------|
| Bare key | `TEAMS-1234` | Yes |
| Bracketed key | `[TEAMS-1234]` | Yes |
| Atlassian URL | `stackoverflow.atlassian.net/browse/TEAMS-1234` | Yes |
| Lowercase | `teams-1234` | No (project keys are uppercase) |

Regex: `\[?([A-Z][A-Z0-9]{1,9}-\d+)\]?`

## Scripts

### `fetch_prs.py` — Data Collection

Fetches merged PR metadata from GitHub via GraphQL.

```bash
# All repos, last 6 months (default)
python fetch_prs.py

# Specific repos only
python fetch_prs.py --repos StackOverflow data-core-metrics

# Custom date range
python fetch_prs.py --since 2025-01-01 --until 2025-06-30

# Limit repos (for testing)
python fetch_prs.py --max-repos 5

# Resume after interruption
python fetch_prs.py --resume

# Skip specific repos
python fetch_prs.py --exclude-repos legacy-monolith old-service
```

| Flag | Default | Description |
|------|---------|-------------|
| `--org` | `StackEng` | GitHub org to scan |
| `--repos` | all | Specific repos to fetch |
| `--exclude-repos` | none | Repos to skip |
| `--no-exclude-archived` | exclude | Include archived repos |
| `--since` | 6 months ago | Earliest merge date (ISO) |
| `--until` | now | Latest merge date (ISO) |
| `--max-repos` | unlimited | Cap repo count (testing) |
| `--resume` | off | Skip already-fetched repos |
| `--rate-limit-buffer` | 500 | Pause threshold for GitHub API |
| `--output-dir` | `data/raw/` | Where to write JSONL files |
| `-v` | off | Verbose logging |

**Environment variables** (override flags per 12-factor):
- `JIRA_ANALYSIS_ORG` — override `--org`
- `JIRA_ANALYSIS_SINCE` — override `--since`

**Output:** One JSONL file per repo in `data/raw/{RepoName}.jsonl`

**Rate limiting:** The script checks remaining quota inline with every GraphQL response. If remaining drops below `--rate-limit-buffer`, it sleeps until the reset window. Retries transient failures (502/503) with exponential backoff.

**Large repos:** StackOverflow has 23k+ merged PRs. GitHub GraphQL only supports ordering by `UPDATED_AT`, not `MERGED_AT`, so the script must paginate through all pages to find PRs in your date range. Use `--resume` if interrupted.

### `analyze_jira.py` — Analysis

Reads raw JSONL, detects Jira tickets, classifies no-ticket PRs.

```bash
# Analyze all fetched data
python analyze_jira.py

# Narrow to specific repos
python analyze_jira.py --repos StackOverflow

# Further date filtering (on already-fetched data)
python analyze_jira.py --since 2026-01-01

# Include bot PRs in compliance rate
python analyze_jira.py --no-exclude-bots
```

| Flag | Default | Description |
|------|---------|-------------|
| `--input-dir` | `data/raw/` | Directory with JSONL files |
| `--output` | `data/analysis.json` | Full analysis output |
| `--since` | none | Post-fetch date filter |
| `--until` | none | Post-fetch date filter |
| `--repos` | all | Repos to analyze |
| `--no-exclude-bots` | exclude bots | Include bots in compliance rate |
| `--save-no-jira` | `data/no_jira.json` | Where to write classified no-ticket PRs |
| `-v` | off | Verbose logging |

**Output:** `data/analysis.json` with org-wide stats, per-repo breakdown, quarterly trends, and category counts.

### `report.py` — Report Generation

Generates a formatted markdown report from analysis data.

```bash
# Both markdown and JSON
python report.py --format both

# Markdown only, top 10 repos
python report.py --format markdown --top-n 10

# Only rank repos with 50+ PRs
python report.py --min-prs 50
```

| Flag | Default | Description |
|------|---------|-------------|
| `--input` | `data/analysis.json` | Analysis file to read |
| `--output` | `output/report.md` | Markdown report path |
| `--format` | `both` | `markdown`, `json`, or `both` |
| `--top-n` | 20 | Repos shown in rankings |
| `--min-prs` | 10 | Minimum PRs for ranking inclusion |
| `-v` | off | Verbose logging |

**Report sections:**
1. Executive Summary — org-wide rate, trend direction
2. Quarterly Trends — compliance over time
3. Top Repos by Volume — largest repos
4. Most/Least Compliant — ranked by compliance rate
5. No-Jira Categories — why PRs lack tickets
6. Jira Projects Referenced — which teams track best
7. Recommendations — auto-generated action items

## No-Jira PR Classification

PRs without a Jira ticket are classified into categories:

| Category | How Detected |
|----------|-------------|
| `bot/automated-dependency` | Author is dependabot/renovate/so-tooling, or title matches bump/update/upgrade |
| `revert` | Title starts with "revert" |
| `ci/chore` | Title starts with "ci:" or "chore:" |
| `explicitly-no-jira` | Body/title contains NO_JIRA, no-ticket, etc. |
| `fix/hotfix` | Title contains fix/bug/hotfix/patch |
| `merge/sync` | Title contains merge/sync/cherry-pick |
| `release/deploy` | Title contains release/deploy |
| `cleanup/refactor` | Title contains cleanup/refactor/lint/format/typo |
| `test` | Title contains test/spec/coverage |
| `docs` | Title contains doc/readme/changelog |
| `feature-flag/experiment` | Title contains enable/disable/toggle/flag/rollout |
| `wip/draft` | Title contains wip/draft |
| `feature/other-unlabeled` | No pattern match — **the actionable compliance gap** |

## Directory Structure

```
pr_jira_analysis_code/
├── fetch_prs.py          # Step 1: fetch from GitHub
├── analyze_jira.py       # Step 2: detect tickets, classify
├── report.py             # Step 3: generate report
├── gh_client.py          # GitHub GraphQL client (rate limiting, retry)
├── classifiers.py        # Jira regex + PR classification logic
├── data/
│   ├── raw/              # Per-repo JSONL files (gitignored)
│   ├── repos.json        # Repo manifest from fetch
│   ├── fetch_state.json  # Resume checkpoint (gitignored)
│   ├── analysis.json     # Analysis output (gitignored)
│   └── no_jira.json      # Classified no-ticket PRs (gitignored)
├── output/
│   ├── report.md         # Markdown report (gitignored)
│   └── report.json       # Machine-readable summary (gitignored)
├── raw_prs.jsonl         # Legacy: original single-repo data
├── analyze.py            # Legacy: original single-repo analyzer
├── deep_analysis.py      # Legacy: original deep-dive script
└── final_report.py       # Legacy: original report script
```

## Examples

**Quick test with one repo:**
```bash
python fetch_prs.py --repos StackOverflow --since 2026-05-01 --verbose
python analyze_jira.py --repos StackOverflow --verbose
python report.py
cat output/report.md
```

**Full org scan with custom window:**
```bash
python fetch_prs.py --since 2026-01-01 --verbose
# (takes 5-15 min depending on org size)
python analyze_jira.py --verbose
python report.py --format both --top-n 30
```

**Investigate no-ticket PRs:**
```bash
python analyze_jira.py --verbose
# Then inspect data/no_jira.json for the full list with categories
python -c "import json; d=json.load(open('data/no_jira.json')); print(len([p for p in d if p['category']=='feature/other-unlabeled']))"
```
