#!/usr/bin/env python3
"""Analyze fetched PR data for Jira ticket compliance."""

import argparse
import json
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from classifiers import (
    classify_no_jira_pr,
    detect_jira_tickets,
    extract_jira_projects,
)

logger = logging.getLogger(__name__)


def quarter_from_date(dt_str: str) -> str:
    dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    q = (dt.month - 1) // 3 + 1
    return f"{dt.year}-Q{q}"


def analyze_repo(repo_file: Path, since: str | None, until: str | None) -> dict:
    """Analyze a single repo's JSONL file."""
    from classifiers import BOT_AUTHORS

    since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc) if since else None
    until_dt = datetime.fromisoformat(until).replace(tzinfo=timezone.utc) if until else None

    prs = []
    with open(repo_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                prs.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed line in %s", repo_file.name)

    if since_dt or until_dt:
        filtered = []
        for pr in prs:
            merged_at = datetime.fromisoformat(pr['mergedAt'].replace('Z', '+00:00'))
            if since_dt and merged_at < since_dt:
                continue
            if until_dt and merged_at > until_dt:
                continue
            filtered.append(pr)
        prs = filtered

    repo_name = repo_file.stem
    total = len(prs)
    with_jira = []
    without_jira = []
    jira_projects = Counter()
    categories = Counter()
    bot_count = 0

    for pr in prs:
        if pr.get('author', '').lower() in BOT_AUTHORS:
            bot_count += 1

        tickets = detect_jira_tickets(pr.get('title', ''), pr.get('body', ''))
        if tickets:
            pr['jira_keys'] = tickets
            with_jira.append(pr)
            for project in extract_jira_projects(tickets):
                jira_projects[project] += 1
        else:
            pr['jira_keys'] = []
            category = classify_no_jira_pr(pr)
            pr['category'] = category
            categories[category] += 1
            without_jira.append(pr)

    compliance_rate = len(with_jira) / total if total > 0 else 0.0
    non_bot_total = total - bot_count
    non_bot_with_jira = sum(
        1 for pr in with_jira if pr.get('author', '').lower() not in BOT_AUTHORS
    )
    compliance_rate_excl_bots = non_bot_with_jira / non_bot_total if non_bot_total > 0 else 0.0

    dates = [pr['mergedAt'] for pr in prs if pr.get('mergedAt')]
    date_range = {
        'earliest': min(dates) if dates else None,
        'latest': max(dates) if dates else None,
    }

    return {
        'repo': repo_name,
        'total_prs': total,
        'with_jira': len(with_jira),
        'without_jira': len(without_jira),
        'compliance_rate': compliance_rate,
        'compliance_rate_excl_bots': compliance_rate_excl_bots,
        'jira_projects': dict(jira_projects.most_common()),
        'categories': dict(categories.most_common()),
        'bot_prs': bot_count,
        'date_range': date_range,
        'no_jira_prs': without_jira,
    }


def compute_quarterly_trends(repo_results: list[dict]) -> dict:
    """Group all PRs by quarter and compute compliance rate per quarter."""
    quarters: dict[str, dict] = {}

    for result in repo_results:
        all_prs_file = Path(result.get('_source_file', ''))
        if not all_prs_file.exists():
            continue

        with open(all_prs_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                pr = json.loads(line)
                q = quarter_from_date(pr['mergedAt'])
                if q not in quarters:
                    quarters[q] = {'total': 0, 'with_jira': 0}
                quarters[q]['total'] += 1
                tickets = detect_jira_tickets(pr.get('title', ''), pr.get('body', ''))
                if tickets:
                    quarters[q]['with_jira'] += 1

    for q_data in quarters.values():
        q_data['rate'] = q_data['with_jira'] / q_data['total'] if q_data['total'] > 0 else 0.0

    return dict(sorted(quarters.items()))


def aggregate_results(repo_results: list[dict]) -> dict:
    """Combine per-repo results into org-wide statistics."""
    total_prs = sum(r['total_prs'] for r in repo_results)
    total_with_jira = sum(r['with_jira'] for r in repo_results)
    total_without_jira = sum(r['without_jira'] for r in repo_results)
    total_bot_prs = sum(r['bot_prs'] for r in repo_results)

    org_jira_projects = Counter()
    org_categories = Counter()
    for r in repo_results:
        for proj, count in r['jira_projects'].items():
            org_jira_projects[proj] += count
        for cat, count in r['categories'].items():
            org_categories[cat] += count

    non_bot_total = total_prs - total_bot_prs
    non_bot_with_jira = total_with_jira - sum(
        r['bot_prs'] - sum(
            1 for pr in r['no_jira_prs'] if pr.get('author', '').lower() in {
                'dependabot', 'dependabot[bot]', 'renovate', 'renovate[bot]',
                'github-actions', 'github-actions[bot]', 'so-tooling',
            }
        )
        for r in repo_results
    )
    compliance_excl_bots = non_bot_with_jira / non_bot_total if non_bot_total > 0 else 0.0

    per_repo_summary = []
    for r in repo_results:
        if r['total_prs'] == 0:
            continue
        per_repo_summary.append({
            'repo': r['repo'],
            'total_prs': r['total_prs'],
            'with_jira': r['with_jira'],
            'compliance_rate': r['compliance_rate'],
            'compliance_rate_excl_bots': r['compliance_rate_excl_bots'],
            'top_jira_projects': list(r['jira_projects'].keys())[:5],
            'date_range': r['date_range'],
        })

    per_repo_summary.sort(key=lambda x: x['total_prs'], reverse=True)

    return {
        'org_summary': {
            'total_repos': len(repo_results),
            'repos_with_prs': sum(1 for r in repo_results if r['total_prs'] > 0),
            'total_prs': total_prs,
            'with_jira': total_with_jira,
            'without_jira': total_without_jira,
            'compliance_rate': total_with_jira / total_prs if total_prs > 0 else 0.0,
            'compliance_rate_excl_bots': compliance_excl_bots,
            'bot_prs': total_bot_prs,
        },
        'jira_projects': dict(org_jira_projects.most_common()),
        'category_breakdown': dict(org_categories.most_common()),
        'per_repo': per_repo_summary,
    }


@dataclass
class ContributorResult:
    username: str = ""
    total_prs: int = 0
    with_jira: int = 0
    compliance_rate: float = 0.0
    per_repo: list[dict] = field(default_factory=list)
    category_breakdown: dict = field(default_factory=dict)
    quarterly_trend: dict = field(default_factory=dict)
    top_jira_projects: list[tuple] = field(default_factory=list)
    elapsed: float = 0.0
    errors: list[str] = field(default_factory=list)


def collect_contributors(raw_dir: Path, bot_authors: frozenset | None = None) -> list[str]:
    """Return sorted unique human contributor usernames from all raw JSONL files."""
    from classifiers import BOT_AUTHORS as _DEFAULT_BOTS
    bots = bot_authors if bot_authors is not None else _DEFAULT_BOTS
    seen: set[str] = set()
    for jsonl_file in sorted(raw_dir.glob("*.jsonl")):
        with open(jsonl_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    pr = json.loads(line)
                except json.JSONDecodeError:
                    continue
                author = pr.get("author", "")
                if author and author.lower() not in bots:
                    seen.add(author)
    return sorted(seen, key=str.lower)


def analyze_contributor(username: str, raw_dir: Path, since: str | None = None, until: str | None = None) -> ContributorResult:
    """Analyze all PRs for a single contributor across all repos."""
    import time
    from classifiers import detect_jira_tickets, extract_jira_projects, classify_no_jira_pr

    start = time.time()
    result = ContributorResult(username=username)

    since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc) if since else None
    until_dt = datetime.fromisoformat(until).replace(tzinfo=timezone.utc) if until else None

    per_repo: dict[str, dict] = {}
    category_counter: Counter = Counter()
    jira_project_counter: Counter = Counter()
    quarters: dict[str, dict] = {}

    for jsonl_file in sorted(raw_dir.glob("*.jsonl")):
        repo_name = jsonl_file.stem
        with open(jsonl_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    pr = json.loads(line)
                except json.JSONDecodeError:
                    result.errors.append(f"Malformed line in {jsonl_file.name}")
                    continue

                if pr.get("author", "") != username:
                    continue

                merged_at = pr.get("mergedAt", "")
                if merged_at:
                    try:
                        merged_dt = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
                        if since_dt and merged_dt < since_dt:
                            continue
                        if until_dt and merged_dt > until_dt:
                            continue
                    except ValueError:
                        pass

                if repo_name not in per_repo:
                    per_repo[repo_name] = {"total": 0, "with_jira": 0}
                per_repo[repo_name]["total"] += 1
                result.total_prs += 1

                tickets = detect_jira_tickets(pr.get("title", ""), pr.get("body", ""))
                if tickets:
                    result.with_jira += 1
                    per_repo[repo_name]["with_jira"] += 1
                    for project in extract_jira_projects(tickets):
                        jira_project_counter[project] += 1
                else:
                    category = classify_no_jira_pr(pr)
                    category_counter[category] += 1

                if merged_at:
                    try:
                        q = quarter_from_date(merged_at)
                        if q not in quarters:
                            quarters[q] = {"total": 0, "with_jira": 0}
                        quarters[q]["total"] += 1
                        if tickets:
                            quarters[q]["with_jira"] += 1
                    except (ValueError, KeyError):
                        pass

    result.compliance_rate = result.with_jira / result.total_prs if result.total_prs > 0 else 0.0
    result.per_repo = [
        {
            "repo": repo,
            "total": stats["total"],
            "with_jira": stats["with_jira"],
            "rate": stats["with_jira"] / stats["total"] if stats["total"] > 0 else 0.0,
        }
        for repo, stats in sorted(per_repo.items(), key=lambda x: -x[1]["total"])
    ]
    result.category_breakdown = dict(category_counter.most_common())
    result.top_jira_projects = jira_project_counter.most_common(10)

    for q_data in quarters.values():
        q_data["rate"] = q_data["with_jira"] / q_data["total"] if q_data["total"] > 0 else 0.0
    result.quarterly_trend = dict(sorted(quarters.items()))

    result.elapsed = time.time() - start
    return result


@dataclass
class AnalyzeConfig:
    input_dir: Path = field(default_factory=lambda: Path(__file__).parent / "data" / "raw")
    output: Path = field(default_factory=lambda: Path(__file__).parent / "data" / "analysis.json")
    since: str | None = None
    until: str | None = None
    repos: list[str] | None = None
    exclude_bots: bool = True
    save_no_jira: Path = field(default_factory=lambda: Path(__file__).parent / "data" / "no_jira.json")
    verbose: bool = False


@dataclass
class AnalyzeResult:
    repos_analyzed: int = 0
    total_prs: int = 0
    compliance_rate: float = 0.0
    elapsed: float = 0.0
    errors: list[str] = field(default_factory=list)


def run_analysis(
    config: AnalyzeConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> AnalyzeResult:
    """Run analysis programmatically.

    on_progress(current, total, label) called after each repo file is processed.
    """
    import time

    start = time.time()
    result = AnalyzeResult()

    jsonl_files = sorted(config.input_dir.glob("*.jsonl"))
    if config.repos:
        repo_set = {r.lower() for r in config.repos}
        jsonl_files = [f for f in jsonl_files if f.stem.lower() in repo_set]

    if not jsonl_files:
        result.errors.append(f"No JSONL files found in {config.input_dir}")
        return result

    total_files = len(jsonl_files)
    repo_results = []

    for i, repo_file in enumerate(jsonl_files, 1):
        repo_result = analyze_repo(repo_file, config.since, config.until)
        repo_result["_source_file"] = str(repo_file)
        repo_results.append(repo_result)

        if on_progress:
            on_progress(i, total_files, repo_file.stem)

    quarterly_trends = compute_quarterly_trends(repo_results)
    aggregate = aggregate_results(repo_results)
    aggregate["quarterly_trends"] = quarterly_trends
    aggregate["generated_at"] = datetime.now(timezone.utc).isoformat()
    aggregate["config"] = {
        "since": config.since,
        "until": config.until,
        "exclude_bots": config.exclude_bots,
    }

    for r in repo_results:
        r.pop("_source_file", None)

    config.output.parent.mkdir(parents=True, exist_ok=True)
    with open(config.output, "w") as f:
        json.dump(aggregate, f, indent=2)

    all_no_jira = []
    for r in repo_results:
        all_no_jira.extend(r["no_jira_prs"])

    with open(config.save_no_jira, "w") as f:
        json.dump(all_no_jira, f, indent=2)

    result.repos_analyzed = len(repo_results)
    result.total_prs = aggregate["org_summary"]["total_prs"]
    result.compliance_rate = aggregate["org_summary"]["compliance_rate"]
    result.elapsed = time.time() - start
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze PRs for Jira ticket compliance")
    parser.add_argument(
        '--input-dir', type=Path, default=Path(__file__).parent / 'data' / 'raw',
        help="Directory containing per-repo JSONL files",
    )
    parser.add_argument(
        '--output', type=Path, default=Path(__file__).parent / 'data' / 'analysis.json',
        help="Output analysis JSON file",
    )
    parser.add_argument('--since', default=None, help="Only analyze PRs merged after this date")
    parser.add_argument('--until', default=None, help="Only analyze PRs merged before this date")
    parser.add_argument('--repos', nargs='*', help="Specific repos to analyze (default: all)")
    parser.add_argument(
        '--no-exclude-bots', dest='exclude_bots', action='store_false',
        help="Include bot PRs in compliance rate",
    )
    parser.add_argument(
        '--save-no-jira', type=Path, default=Path(__file__).parent / 'data' / 'no_jira.json',
        help="Save classified no-Jira PRs",
    )
    parser.add_argument('--verbose', '-v', action='store_true')
    parser.set_defaults(exclude_bots=True)
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
    )

    jsonl_files = sorted(args.input_dir.glob('*.jsonl'))
    if args.repos:
        repo_set = {r.lower() for r in args.repos}
        jsonl_files = [f for f in jsonl_files if f.stem.lower() in repo_set]

    if not jsonl_files:
        logger.error("No JSONL files found in %s", args.input_dir)
        return

    logger.info("Analyzing %d repo files...", len(jsonl_files))
    repo_results = []

    for repo_file in jsonl_files:
        result = analyze_repo(repo_file, args.since, args.until)
        result['_source_file'] = str(repo_file)
        repo_results.append(result)
        logger.info("  %s: %d PRs, %.1f%% compliance",
                    result['repo'], result['total_prs'], result['compliance_rate'] * 100)

    quarterly_trends = compute_quarterly_trends(repo_results)
    aggregate = aggregate_results(repo_results)
    aggregate['quarterly_trends'] = quarterly_trends
    aggregate['generated_at'] = datetime.now(timezone.utc).isoformat()
    aggregate['config'] = {
        'since': args.since,
        'until': args.until,
        'exclude_bots': args.exclude_bots,
    }

    for r in repo_results:
        r.pop('_source_file', None)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, 'w') as f:
        json.dump(aggregate, f, indent=2)
    logger.info("Analysis written to %s", args.output)

    all_no_jira = []
    for r in repo_results:
        all_no_jira.extend(r['no_jira_prs'])

    with open(args.save_no_jira, 'w') as f:
        json.dump(all_no_jira, f, indent=2)
    logger.info("No-Jira PRs (%d) written to %s", len(all_no_jira), args.save_no_jira)

    print(f"\n{'='*60}")
    print(f"  Org-wide Jira Compliance Summary")
    print(f"{'='*60}")
    print(f"  Total PRs analyzed:  {aggregate['org_summary']['total_prs']:,}")
    print(f"  With Jira ticket:    {aggregate['org_summary']['with_jira']:,} "
          f"({aggregate['org_summary']['compliance_rate']*100:.1f}%)")
    print(f"  Without Jira ticket: {aggregate['org_summary']['without_jira']:,}")
    print(f"  Bot PRs:             {aggregate['org_summary']['bot_prs']:,}")
    print(f"  Compliance (excl bots): {aggregate['org_summary']['compliance_rate_excl_bots']*100:.1f}%")
    print(f"  Repos analyzed:      {aggregate['org_summary']['repos_with_prs']}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
