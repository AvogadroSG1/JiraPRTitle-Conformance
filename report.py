#!/usr/bin/env python3
"""Generate Jira compliance report from analysis data."""

import argparse
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def render_executive_summary(data: dict) -> str:
    summary = data['org_summary']
    trends = data.get('quarterly_trends', {})
    config = data.get('config', {})

    trend_note = ""
    if len(trends) >= 2:
        sorted_qs = sorted(trends.keys())
        latest = trends[sorted_qs[-1]]['rate']
        previous = trends[sorted_qs[-2]]['rate']
        diff = (latest - previous) * 100
        direction = "Improving" if diff > 0 else "Declining"
        trend_note = f"- **Trend:** {direction} ({diff:+.1f}% from previous quarter)"

    since_str = config.get('since', 'all time')
    until_str = config.get('until', 'now')

    lines = [
        "## Executive Summary\n",
        f"- **Org-wide compliance rate:** {summary['compliance_rate']*100:.1f}% "
        f"({summary['compliance_rate_excl_bots']*100:.1f}% excluding bots)",
        f"- **Total PRs analyzed:** {summary['total_prs']:,} across {summary['repos_with_prs']} repos",
        f"- **Period:** {since_str} to {until_str}",
        f"- **Bot PRs excluded from adjusted rate:** {summary['bot_prs']:,}",
    ]
    if trend_note:
        lines.append(trend_note)

    return '\n'.join(lines)


def render_quarterly_trends(trends: dict) -> str:
    if not trends:
        return "## Quarterly Trends\n\nInsufficient data for trend analysis."

    lines = [
        "## Quarterly Trends\n",
        "| Quarter | Total PRs | With Jira | Compliance |",
        "|---------|-----------|-----------|------------|",
    ]

    for q in sorted(trends.keys()):
        t = trends[q]
        lines.append(
            f"| {q} | {t['total']:,} | {t['with_jira']:,} | {t['rate']*100:.1f}% |"
        )

    return '\n'.join(lines)


def render_repo_rankings(repos: list[dict], top_n: int, min_prs: int) -> str:
    eligible = [r for r in repos if r['total_prs'] >= min_prs]
    if not eligible:
        return "## Repo Rankings\n\nNo repos meet the minimum PR threshold."

    by_compliance = sorted(eligible, key=lambda x: x['compliance_rate'], reverse=True)

    lines = [
        f"## Top {top_n} Most Compliant Repos (min {min_prs} PRs)\n",
        "| # | Repo | PRs | Compliance | Top Projects |",
        "|---|------|-----|------------|--------------|",
    ]
    for i, r in enumerate(by_compliance[:top_n], 1):
        projects = ', '.join(r.get('top_jira_projects', [])[:3])
        lines.append(
            f"| {i} | {r['repo']} | {r['total_prs']:,} | "
            f"{r['compliance_rate']*100:.1f}% | {projects} |"
        )

    lines.extend([
        "",
        f"## Bottom {top_n} Least Compliant Repos (min {min_prs} PRs)\n",
        "| # | Repo | PRs | Compliance | Top Projects |",
        "|---|------|-----|------------|--------------|",
    ])
    for i, r in enumerate(by_compliance[-top_n:], 1):
        projects = ', '.join(r.get('top_jira_projects', [])[:3])
        lines.append(
            f"| {i} | {r['repo']} | {r['total_prs']:,} | "
            f"{r['compliance_rate']*100:.1f}% | {projects} |"
        )

    return '\n'.join(lines)


def render_category_breakdown(categories: dict) -> str:
    if not categories:
        return "## No-Jira PR Categories\n\nNo categorized PRs."

    total = sum(categories.values())
    lines = [
        "## No-Jira PR Categories\n",
        "| Category | Count | % of No-Jira |",
        "|----------|-------|--------------|",
    ]
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        pct = count / total * 100 if total > 0 else 0
        lines.append(f"| {cat} | {count:,} | {pct:.1f}% |")

    return '\n'.join(lines)


def render_jira_projects(projects: dict) -> str:
    if not projects:
        return "## Jira Projects Referenced\n\nNo Jira tickets detected."

    lines = [
        "## Jira Projects Referenced\n",
        "| Project | References |",
        "|---------|-----------|",
    ]
    for proj, count in sorted(projects.items(), key=lambda x: -x[1])[:30]:
        lines.append(f"| {proj} | {count:,} |")

    return '\n'.join(lines)


def render_recommendations(data: dict) -> str:
    summary = data['org_summary']
    categories = data.get('category_breakdown', {})

    recs = ["## Recommendations\n"]

    bot_pct = summary['bot_prs'] / summary['total_prs'] * 100 if summary['total_prs'] > 0 else 0
    if bot_pct > 10:
        recs.append(
            f"1. **Exempt bot PRs from compliance metrics** — {bot_pct:.0f}% of PRs are automated "
            f"(dependabot, renovate, so-tooling). These legitimately lack Jira tickets."
        )

    unlabeled = categories.get('feature/other-unlabeled', 0)
    if unlabeled > 0:
        recs.append(
            f"2. **Address unclassified PRs** — {unlabeled:,} PRs lack both a Jira ticket and any "
            f"recognizable category. These represent the true compliance gap."
        )

    fixes = categories.get('fix/hotfix', 0)
    if fixes > 20:
        recs.append(
            f"3. **Require tickets for hotfixes** — {fixes:,} fix PRs lack Jira links. "
            f"Even urgent fixes benefit from ticket tracking for postmortems."
        )

    # Repo-specific recommendations
    repos = data.get('per_repo', [])
    low_compliance = [r for r in repos if r['total_prs'] >= 50 and r['compliance_rate'] < 0.5]
    if low_compliance:
        names = ', '.join(r['repo'] for r in low_compliance[:5])
        recs.append(
            f"4. **Team outreach for low-compliance repos** — These repos have <50% compliance "
            f"with 50+ PRs: {names}"
        )

    if len(recs) == 1:
        recs.append("No specific recommendations — compliance looks healthy.")

    return '\n'.join(recs)


def render_largest_repos(repos: list[dict], top_n: int) -> str:
    by_volume = sorted(repos, key=lambda x: x['total_prs'], reverse=True)[:top_n]
    if not by_volume:
        return ""

    lines = [
        f"## Top {top_n} Repos by PR Volume\n",
        "| # | Repo | Total PRs | Compliance |",
        "|---|------|-----------|------------|",
    ]
    for i, r in enumerate(by_volume, 1):
        lines.append(
            f"| {i} | {r['repo']} | {r['total_prs']:,} | {r['compliance_rate']*100:.1f}% |"
        )

    return '\n'.join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Jira compliance report")
    parser.add_argument(
        '--input', type=Path, default=Path(__file__).parent / 'data' / 'analysis.json',
        help="Analysis JSON from analyze_jira.py",
    )
    parser.add_argument(
        '--output', type=Path, default=Path(__file__).parent / 'output' / 'report.md',
        help="Output markdown report",
    )
    parser.add_argument(
        '--format', choices=['markdown', 'json', 'both'], default='both',
        help="Output format",
    )
    parser.add_argument('--top-n', type=int, default=20, help="Repos in rankings")
    parser.add_argument('--min-prs', type=int, default=10, help="Minimum PRs for ranking inclusion")
    parser.add_argument('--verbose', '-v', action='store_true')
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
    )

    if not args.input.exists():
        logger.error("Analysis file not found: %s", args.input)
        logger.error("Run analyze_jira.py first.")
        return

    with open(args.input) as f:
        data = json.load(f)

    generated = data.get('generated_at', 'unknown')
    header = (
        f"# Jira Ticket Compliance Report — StackEng\n\n"
        f"**Generated:** {generated[:10]} | "
        f"**Period:** {data.get('config', {}).get('since', 'all time')} to "
        f"{data.get('config', {}).get('until', 'present')}\n"
    )

    sections = [
        header,
        render_executive_summary(data),
        render_quarterly_trends(data.get('quarterly_trends', {})),
        render_largest_repos(data.get('per_repo', []), args.top_n),
        render_repo_rankings(data.get('per_repo', []), args.top_n, args.min_prs),
        render_category_breakdown(data.get('category_breakdown', {})),
        render_jira_projects(data.get('jira_projects', {})),
        render_recommendations(data),
    ]

    report_md = '\n\n---\n\n'.join(s for s in sections if s)

    args.output.parent.mkdir(parents=True, exist_ok=True)

    if args.format in ('markdown', 'both'):
        with open(args.output, 'w') as f:
            f.write(report_md)
        logger.info("Markdown report written to %s", args.output)

    if args.format in ('json', 'both'):
        json_output = args.output.with_suffix('.json')
        summary = {
            'generated_at': generated,
            'org_summary': data['org_summary'],
            'quarterly_trends': data.get('quarterly_trends', {}),
            'top_repos': data.get('per_repo', [])[:args.top_n],
            'category_breakdown': data.get('category_breakdown', {}),
            'jira_projects': dict(list(data.get('jira_projects', {}).items())[:20]),
        }
        with open(json_output, 'w') as f:
            json.dump(summary, f, indent=2)
        logger.info("JSON summary written to %s", json_output)

    print(f"\nReport generated: {args.output}")


if __name__ == '__main__':
    main()
