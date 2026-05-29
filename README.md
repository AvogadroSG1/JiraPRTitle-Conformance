# StackEng Jira Ticket Compliance Scanner

Scans merged PRs across all StackEng GitHub repos and evaluates whether they reference a Jira ticket in the title or body. Produces per-repo and org-wide compliance reports.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package manager)
- `gh` CLI authenticated with access to the StackEng org (`gh auth status`)

## Quick Start

```bash
cd ~/peter_code/scratch_work/pr_jira_analysis_code

# Install dependencies
uv sync

# Run the full pipeline (fetch → analyze → report)
uv run jira-scan

# Or run individual stages
uv run jira-scan --only report
uv run jira-scan --skip-fetch
```

Output lands in `output/report.md` (human-readable) and `output/report.json` (machine-readable).

## Usage

```bash
# Full pipeline with defaults
uv run jira-scan

# Skip fetch, reuse existing data
uv run jira-scan --skip-fetch

# Only regenerate the report
uv run jira-scan --only report

# Custom date range
uv run jira-scan --since 2025-01-01 --until 2025-06-30

# Specific repos
uv run jira-scan --repos StackOverflow data-core-metrics

# Limit repos (for testing)
uv run jira-scan --max-repos 5

# Continue even if a stage fails
uv run jira-scan --continue-on-error

# Verbose output
uv run jira-scan --verbose
```

The `jira-scan` command shows progress bars for each stage (per-repo during fetch, per-file during analysis, per-section during report generation) and a summary table at the end.

## Ticket Detection

The scanner looks for Jira ticket references in both PR **title** and **body**:

| Format | Example | Detected? |
|--------|---------|-----------|
| Bare key | `TEAMS-1234` | Yes |
| Bracketed key | `[TEAMS-1234]` | Yes |
| Atlassian URL | `stackoverflow.atlassian.net/browse/TEAMS-1234` | Yes |
| Lowercase | `teams-1234` | No (project keys are uppercase) |

Regex: `\[?([A-Z][A-Z0-9]{1,9}-\d+)\]?`

## Individual Scripts

The scripts below can also be run standalone via `uv run python <script>` if you need fine-grained control beyond what `jira-scan` exposes.

### `fetch_prs.py` — Data Collection

Fetches merged PR metadata from GitHub via GraphQL.

```bash
# All repos, last 6 months (default)
uv run python fetch_prs.py

# Specific repos only
uv run python fetch_prs.py --repos StackOverflow data-core-metrics

# Custom date range
uv run python fetch_prs.py --since 2025-01-01 --until 2025-06-30

# Limit repos (for testing)
uv run python fetch_prs.py --max-repos 5

# Resume after interruption
uv run python fetch_prs.py --resume

# Skip specific repos
uv run python fetch_prs.py --exclude-repos legacy-monolith old-service
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
uv run python analyze_jira.py

# Narrow to specific repos
uv run python analyze_jira.py --repos StackOverflow

# Further date filtering (on already-fetched data)
uv run python analyze_jira.py --since 2026-01-01

# Include bot PRs in compliance rate
uv run python analyze_jira.py --no-exclude-bots
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
uv run python report.py --format both

# Markdown only, top 10 repos
uv run python report.py --format markdown --top-n 10

# Only rank repos with 50+ PRs
uv run python report.py --min-prs 50
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
├── pyproject.toml        # Project metadata, dependencies, entry point
├── run.py                # Unified orchestrator (jira-scan entry point)
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
└── output/
    ├── report.md         # Markdown report (gitignored)
    └── report.json       # Machine-readable summary (gitignored)
```

## Examples

**Quick test with one repo:**
```bash
uv run jira-scan --repos StackOverflow --since 2026-05-01 --verbose
cat output/report.md
```

**Full org scan with custom window:**
```bash
uv run jira-scan --since 2026-01-01 --verbose
# (takes 5-15 min depending on org size)
```

**Re-generate report after tweaking parameters:**
```bash
uv run jira-scan --only report --top-n 30 --min-prs 50
```

**Investigate no-ticket PRs:**
```bash
uv run jira-scan --skip-fetch --verbose
# Then inspect data/no_jira.json for the full list with categories
uv run python -c "import json; d=json.load(open('data/no_jira.json')); print(len([p for p in d if p['category']=='feature/other-unlabeled']))"
```
