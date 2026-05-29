# Unified Pipeline Entry Point (`run.py`)

**Date:** 2026-05-29  
**Status:** Draft  

## Context

The Jira Compliance Scanner has a three-stage pipeline (`fetch_prs.py` → `analyze_jira.py` → `report.py`) that must be invoked separately. Users must remember the correct order, pass consistent arguments across stages, and have no visibility into progress during long-running fetches. A single entry point with rich progress display solves all three problems.

## Design

### Entry Point: `run.py`

A CLI script that orchestrates all three pipeline stages with granular progress bars using `rich`.

### CLI Interface

```
python run.py                              # Full pipeline with defaults
python run.py --since 2025-01-01           # Custom date range (passed to fetch + analyze)
python run.py --until 2026-01-01           # Custom end date
python run.py --org StackEng               # GitHub org (default: StackEng)
python run.py --skip-fetch                 # Skip fetch, reuse data/raw/
python run.py --skip-analyze               # Skip analyze, reuse data/analysis.json
python run.py --only fetch                 # Run only the fetch stage
python run.py --only analyze               # Run only the analyze stage
python run.py --only report                # Run only the report stage
python run.py --repos repo1 repo2          # Specific repos only
python run.py --format both                # Report format (markdown/json/both)
python run.py --verbose                    # Debug logging
python run.py --continue-on-error          # Don't abort if a stage fails
```

**Flag semantics:**
- `--skip-fetch` and `--skip-analyze` remove stages from the pipeline
- `--only <stage>` runs exactly one stage (mutually exclusive with `--skip-*`)
- All other flags pass through to the relevant stage(s)

### Progress Display

Uses `rich.progress.Progress` with a group layout showing one task bar per active stage:

```
┌─ Jira Compliance Scanner ──────────────────────────────┐
│                                                         │
│  Fetch    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  12/47 repos  │
│           Currently: StackOverflow                      │
│                                                         │
│  Analyze  ━━━━━━━━━━━╸                     waiting...   │
│                                                         │
│  Report   ━━━━━━━━━━━╸                     waiting...   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

After each stage completes, a summary panel replaces its progress bar:

```
  Fetch    ✓ 47 repos · 2,341 PRs fetched · 1m 23s
```

**Progress granularity per stage:**

| Stage | Unit | Total Known At |
|-------|------|----------------|
| Fetch | repos | After `discover_repos()` returns |
| Analyze | JSONL files | After listing `data/raw/*.jsonl` |
| Report | sections (7 fixed) | Immediately |

### Module Refactoring

Each module exposes a new callable API alongside its existing `main()`:

#### `fetch_prs.py`

```python
@dataclass
class FetchConfig:
    org: str = "StackEng"
    repos: list[str] | None = None
    exclude_repos: list[str] | None = None
    since: str | None = None  # ISO date or relative
    until: str | None = None
    output_dir: Path = Path("data/raw")
    resume: bool = True
    rate_limit_buffer: int = 500
    max_repos: int | None = None
    verbose: bool = False

@dataclass
class FetchResult:
    repos_fetched: int
    total_prs: int
    elapsed: float  # seconds
    errors: list[str]

def run_fetch(config: FetchConfig, on_progress: Callable[[int, int, str], None] | None = None) -> FetchResult:
    """
    Run the fetch pipeline programmatically.
    
    on_progress(current, total, label) called after each repo completes.
    """
```

#### `analyze_jira.py`

```python
@dataclass
class AnalyzeConfig:
    input_dir: Path = Path("data/raw")
    output: Path = Path("data/analysis.json")
    since: str | None = None
    until: str | None = None
    repos: list[str] | None = None
    exclude_bots: bool = True
    save_no_jira: Path = Path("data/no_jira.json")
    verbose: bool = False

@dataclass  
class AnalyzeResult:
    repos_analyzed: int
    total_prs: int
    compliance_rate: float
    elapsed: float
    errors: list[str]

def run_analysis(config: AnalyzeConfig, on_progress: Callable[[int, int, str], None] | None = None) -> AnalyzeResult:
    """
    Run analysis programmatically.
    
    on_progress(current, total, label) called after each repo file is processed.
    """
```

#### `report.py`

```python
@dataclass
class ReportConfig:
    input: Path = Path("data/analysis.json")
    output: Path = Path("output/report.md")
    format: str = "both"  # markdown | json | both
    top_n: int = 20
    min_prs: int = 10
    verbose: bool = False

@dataclass
class ReportResult:
    output_files: list[Path]
    elapsed: float
    errors: list[str]

def run_report(config: ReportConfig, on_progress: Callable[[int, int, str], None] | None = None) -> ReportResult:
    """
    Run report generation programmatically.
    
    on_progress(current, total, section_name) called after each section renders.
    """
```

### Orchestration Flow in `run.py`

```python
def main():
    args = parse_args()
    stages = resolve_stages(args)  # list of ("fetch", "analyze", "report")
    
    with Progress(...) as progress:
        # Create task bars for each active stage
        tasks = {stage: progress.add_task(stage, total=None) for stage in stages}
        
        if "fetch" in stages:
            result = run_fetch(fetch_config, on_progress=make_callback(progress, tasks["fetch"]))
            show_summary_panel("fetch", result)
        
        if "analyze" in stages:
            result = run_analysis(analyze_config, on_progress=make_callback(progress, tasks["analyze"]))
            show_summary_panel("analyze", result)
        
        if "report" in stages:
            result = run_report(report_config, on_progress=make_callback(progress, tasks["report"]))
            show_summary_panel("report", result)
    
    print_final_summary()
```

### Error Handling

- If a stage raises an exception and `--continue-on-error` is not set: print a rich error panel with traceback and exit with code 1.
- If `--continue-on-error` is set: log the error, mark the stage as failed in the summary, and proceed to the next stage (if inputs exist).
- The `FetchResult`/`AnalyzeResult`/`ReportResult` dataclasses carry an `errors` list for non-fatal issues (e.g., a single repo timing out during fetch).

### Dependencies

Add `rich` as the sole new dependency:

```
# requirements.txt
rich>=13.0
```

### File Changes Summary

| File | Change |
|------|--------|
| `run.py` | **New** — orchestrator entry point |
| `fetch_prs.py` | Add `FetchConfig`, `FetchResult`, `run_fetch()` |
| `analyze_jira.py` | Add `AnalyzeConfig`, `AnalyzeResult`, `run_analysis()` |
| `report.py` | Add `ReportConfig`, `ReportResult`, `run_report()` |
| `requirements.txt` | **New** — `rich>=13.0` |

Existing `main()` functions and CLI interfaces remain untouched.

## Verification

1. `pip install rich` and run `python run.py --verbose` end-to-end
2. Verify progress bars update per-repo during fetch
3. Test `--skip-fetch` reuses existing data
4. Test `--only report` runs just the report stage
5. Test error handling: rename a data file and confirm error panel displays
6. Confirm existing scripts still work standalone: `python fetch_prs.py --help`
