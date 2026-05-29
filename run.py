#!/usr/bin/env python3
"""Unified entry point for the Jira Compliance Scanner pipeline."""

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from analyze_jira import AnalyzeConfig, AnalyzeResult, run_analysis
from fetch_prs import FetchConfig, FetchResult, run_fetch
from report import ReportConfig, ReportResult, run_report

console = Console()

BASE_DIR = Path(__file__).parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Jira Compliance Scanner — fetch, analyze, and report in one command",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python run.py                         Full pipeline with defaults
  python run.py --skip-fetch            Reuse existing data, re-analyze and report
  python run.py --only report           Only regenerate the report
  python run.py --since 2025-01-01      Custom date range
""",
    )

    stage_group = parser.add_mutually_exclusive_group()
    stage_group.add_argument("--only", choices=["fetch", "analyze", "report"], help="Run only this stage")
    stage_group.add_argument("--skip-fetch", action="store_true", help="Skip the fetch stage")
    stage_group.add_argument("--skip-analyze", action="store_true", help="Skip the analyze stage")

    parser.add_argument("--org", default="StackEng", help="GitHub org (default: StackEng)")
    parser.add_argument("--repos", nargs="*", help="Specific repos (default: all)")
    parser.add_argument("--exclude-repos", nargs="*", default=[], help="Repos to skip")
    parser.add_argument(
        "--since",
        default=(datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%d"),
        help="PRs merged after this date (default: 6 months ago)",
    )
    parser.add_argument("--until", default=None, help="PRs merged before this date")
    parser.add_argument("--format", choices=["markdown", "json", "both"], default="both", help="Report format")
    parser.add_argument("--top-n", type=int, default=20, help="Repos in rankings")
    parser.add_argument("--min-prs", type=int, default=10, help="Minimum PRs for ranking")
    parser.add_argument("--max-repos", type=int, default=None, help="Limit repos fetched (testing)")
    parser.add_argument("--continue-on-error", action="store_true", help="Don't abort if a stage fails")
    parser.add_argument("--verbose", "-v", action="store_true")

    return parser.parse_args()


def resolve_stages(args: argparse.Namespace) -> list[str]:
    if args.only:
        return [args.only]
    stages = ["fetch", "analyze", "report"]
    if args.skip_fetch:
        stages.remove("fetch")
    if args.skip_analyze:
        stages.remove("analyze")
    return stages


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.0f}s"


def run_pipeline(args: argparse.Namespace) -> None:
    stages = resolve_stages(args)
    overall_start = time.time()

    console.print(
        Panel.fit(
            "[bold blue]Jira Compliance Scanner[/bold blue]\n"
            f"Org: [cyan]{args.org}[/cyan] | Since: [cyan]{args.since}[/cyan] | "
            f"Stages: [cyan]{' → '.join(stages)}[/cyan]",
            border_style="blue",
        )
    )
    console.print()

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/bold]"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TextColumn("[dim]{task.fields[status]}[/dim]"),
        TimeElapsedColumn(),
        console=console,
    )

    results: dict[str, FetchResult | AnalyzeResult | ReportResult] = {}

    with progress:
        task_ids = {}
        for stage in stages:
            task_ids[stage] = progress.add_task(
                f"  {stage.capitalize():<8}",
                total=None,
                status="waiting...",
            )

        if "fetch" in stages:
            tid = task_ids["fetch"]
            progress.update(tid, status="discovering repos...")

            config = FetchConfig(
                org=args.org,
                repos=args.repos,
                exclude_repos=args.exclude_repos or [],
                since=args.since,
                until=args.until,
                output_dir=BASE_DIR / "data" / "raw",
                max_repos=args.max_repos,
                verbose=args.verbose,
            )

            def fetch_progress(current: int, total: int, label: str) -> None:
                progress.update(tid, completed=current, total=total, status=label)

            try:
                result = run_fetch(config, on_progress=fetch_progress)
                results["fetch"] = result
                progress.update(
                    tid, completed=1, total=1,
                    status=f"[green]✓ {result.repos_fetched} repos · {result.total_prs:,} PRs[/green]",
                )
            except Exception as e:
                progress.update(tid, completed=1, total=1, status=f"[red]✗ {e}[/red]")
                if not args.continue_on_error:
                    console.print(f"\n[red bold]Fetch failed:[/red bold] {e}")
                    sys.exit(1)

        if "analyze" in stages:
            tid = task_ids["analyze"]
            progress.update(tid, status="scanning files...")

            config = AnalyzeConfig(
                input_dir=BASE_DIR / "data" / "raw",
                output=BASE_DIR / "data" / "analysis.json",
                since=args.since,
                until=args.until,
                repos=args.repos,
                save_no_jira=BASE_DIR / "data" / "no_jira.json",
                verbose=args.verbose,
            )

            def analyze_progress(current: int, total: int, label: str) -> None:
                progress.update(tid, completed=current, total=total, status=label)

            try:
                result = run_analysis(config, on_progress=analyze_progress)
                results["analyze"] = result
                rate_pct = f"{result.compliance_rate * 100:.1f}%"
                progress.update(
                    tid, completed=1, total=1,
                    status=f"[green]✓ {result.total_prs:,} PRs · {rate_pct} compliance[/green]",
                )
            except Exception as e:
                progress.update(tid, completed=1, total=1, status=f"[red]✗ {e}[/red]")
                if not args.continue_on_error:
                    console.print(f"\n[red bold]Analysis failed:[/red bold] {e}")
                    sys.exit(1)

        if "report" in stages:
            tid = task_ids["report"]
            progress.update(tid, status="rendering...")

            config = ReportConfig(
                input=BASE_DIR / "data" / "analysis.json",
                output=BASE_DIR / "output" / "report.md",
                format=args.format,
                top_n=args.top_n,
                min_prs=args.min_prs,
                verbose=args.verbose,
            )

            def report_progress(current: int, total: int, label: str) -> None:
                progress.update(tid, completed=current, total=total, status=label)

            try:
                result = run_report(config, on_progress=report_progress)
                results["report"] = result
                files = ", ".join(p.name for p in result.output_files)
                progress.update(tid, completed=1, total=1, status=f"[green]✓ {files}[/green]")
            except Exception as e:
                progress.update(tid, completed=1, total=1, status=f"[red]✗ {e}[/red]")
                if not args.continue_on_error:
                    console.print(f"\n[red bold]Report failed:[/red bold] {e}")
                    sys.exit(1)

    console.print()

    total_elapsed = time.time() - overall_start
    summary_table = Table(title="Pipeline Summary", show_header=True, header_style="bold")
    summary_table.add_column("Stage")
    summary_table.add_column("Duration")
    summary_table.add_column("Result")

    for stage in stages:
        r = results.get(stage)
        if r is None:
            summary_table.add_row(stage.capitalize(), "-", "[red]Failed[/red]")
        elif isinstance(r, FetchResult):
            summary_table.add_row(
                "Fetch",
                format_duration(r.elapsed),
                f"{r.repos_fetched} repos · {r.total_prs:,} PRs"
                + (f" · {len(r.errors)} errors" if r.errors else ""),
            )
        elif isinstance(r, AnalyzeResult):
            summary_table.add_row(
                "Analyze",
                format_duration(r.elapsed),
                f"{r.total_prs:,} PRs · {r.compliance_rate * 100:.1f}% compliance",
            )
        elif isinstance(r, ReportResult):
            summary_table.add_row(
                "Report",
                format_duration(r.elapsed),
                " + ".join(p.name for p in r.output_files),
            )

    summary_table.add_section()
    summary_table.add_row("[bold]Total[/bold]", f"[bold]{format_duration(total_elapsed)}[/bold]", "")

    console.print(summary_table)

    if any(hasattr(r, "errors") and r.errors for r in results.values() if r):
        console.print("\n[yellow bold]Warnings:[/yellow bold]")
        for stage, r in results.items():
            if r and r.errors:
                for err in r.errors[:5]:
                    console.print(f"  [{stage}] {err}")


def main():
    args = parse_args()
    run_pipeline(args)


if __name__ == "__main__":
    main()
