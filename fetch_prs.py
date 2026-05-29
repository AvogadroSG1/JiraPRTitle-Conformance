#!/usr/bin/env python3
"""Fetch merged PRs from StackEng GitHub org via GraphQL."""

import argparse
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gh_client import GitHubGraphQLClient, GraphQLError

logger = logging.getLogger(__name__)

REPO_QUERY = """
query($org: String!, $cursor: String) {
  organization(login: $org) {
    repositories(first: 100, after: $cursor, orderBy: {field: NAME, direction: ASC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        isArchived
        pullRequests(states: MERGED) { totalCount }
      }
    }
  }
  rateLimit { remaining resetAt cost }
}
"""

PR_QUERY = """
query($org: String!, $repo: String!, $cursor: String) {
  repository(owner: $org, name: $repo) {
    pullRequests(states: MERGED, first: 100, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        body
        mergedAt
        author { login }
        labels(first: 10) { nodes { name } }
      }
    }
  }
  rateLimit { remaining resetAt cost }
}
"""


def discover_repos(client: GitHubGraphQLClient, org: str, exclude_archived: bool,
                   exclude_repos: list[str]) -> list[dict]:
    """Paginate through org repos. Returns list of repo metadata dicts."""
    repos = []
    cursor = None
    exclude_set = {r.lower() for r in exclude_repos}

    while True:
        variables = {'org': org}
        if cursor:
            variables['cursor'] = cursor

        result = client.query(REPO_QUERY, variables)
        data = result['data']['organization']['repositories']

        for node in data['nodes']:
            if exclude_archived and node['isArchived']:
                continue
            if node['name'].lower() in exclude_set:
                continue
            repos.append({
                'name': node['name'],
                'isArchived': node['isArchived'],
                'mergedPrCount': node['pullRequests']['totalCount'],
            })

        if not data['pageInfo']['hasNextPage']:
            break
        cursor = data['pageInfo']['endCursor']
        logger.info("Discovered %d repos so far...", len(repos))

    return repos


def fetch_prs_for_repo(client: GitHubGraphQLClient, org: str, repo_name: str,
                       since: str | None, until: str | None) -> list[dict]:
    """Fetch all merged PRs for a repo, applying date filters."""
    prs = []
    cursor = None
    since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc) if since else None
    until_dt = datetime.fromisoformat(until).replace(tzinfo=timezone.utc) if until else None

    while True:
        variables = {'org': org, 'repo': repo_name}
        if cursor:
            variables['cursor'] = cursor

        result = client.query(PR_QUERY, variables)
        repo_data = result['data']['repository']
        if not repo_data:
            logger.warning("Repository %s/%s not found or inaccessible", org, repo_name)
            break

        pr_data = repo_data['pullRequests']
        page_prs = pr_data['nodes']

        for node in page_prs:
            if not node.get('mergedAt'):
                continue

            merged_at = datetime.fromisoformat(
                node['mergedAt'].replace('Z', '+00:00')
            )

            if until_dt and merged_at > until_dt:
                continue
            if since_dt and merged_at < since_dt:
                continue
            author_login = ''
            if node.get('author') and node['author'].get('login'):
                author_login = node['author']['login']

            prs.append({
                'repo': repo_name,
                'number': node['number'],
                'title': node['title'] or '',
                'body': (node['body'] or '')[:3000],
                'author': author_login,
                'labels': [lbl['name'] for lbl in (node.get('labels', {}).get('nodes') or [])],
                'mergedAt': node['mergedAt'],
            })

        if not pr_data['pageInfo']['hasNextPage']:
            break

        # Early termination: if ordered by UPDATED_AT DESC and all PRs on this page
        # are older than since, we can't reliably stop (a recently-commented old PR
        # would appear early). So we always paginate fully when since is set.
        cursor = pr_data['pageInfo']['endCursor']

    return prs


def load_fetch_state(state_file: Path) -> dict:
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {'completed_repos': [], 'started_at': None}


def save_fetch_state(state_file: Path, state: dict):
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch merged PRs from GitHub org for Jira compliance analysis"
    )
    parser.add_argument(
        '--org',
        default=os.environ.get('JIRA_ANALYSIS_ORG', 'StackEng'),
        help="GitHub org name (default: StackEng, env: JIRA_ANALYSIS_ORG)",
    )
    parser.add_argument('--repos', nargs='*', help="Specific repos to fetch (default: all)")
    parser.add_argument('--exclude-repos', nargs='*', default=[], help="Repos to skip")
    parser.add_argument(
        '--no-exclude-archived', dest='exclude_archived', action='store_false',
        help="Include archived repos",
    )
    parser.add_argument(
        '--since',
        default=os.environ.get(
            'JIRA_ANALYSIS_SINCE',
            (datetime.now(timezone.utc) - timedelta(days=180)).strftime('%Y-%m-%d'),
        ),
        help="Only fetch PRs merged after this date (ISO format, default: 6 months ago)",
    )
    parser.add_argument('--until', default=None, help="Only fetch PRs merged before this date")
    parser.add_argument(
        '--output-dir', type=Path, default=Path(__file__).parent / 'data' / 'raw',
        help="Directory for per-repo JSONL files",
    )
    parser.add_argument('--max-repos', type=int, default=None, help="Limit number of repos (for testing)")
    parser.add_argument('--resume', action='store_true', help="Resume from last fetch state")
    parser.add_argument(
        '--rate-limit-buffer', type=int, default=500,
        help="Pause when rate limit remaining drops below this",
    )
    parser.add_argument('--verbose', '-v', action='store_true')
    parser.set_defaults(exclude_archived=True)
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    state_file = args.output_dir.parent / 'fetch_state.json'
    client = GitHubGraphQLClient(rate_limit_buffer=args.rate_limit_buffer)

    remaining, reset_at = client.check_rate_limit()
    logger.info("Rate limit: %d remaining (resets %s)", remaining, reset_at)

    state = load_fetch_state(state_file) if args.resume else {'completed_repos': [], 'started_at': None}
    if not state['started_at']:
        state['started_at'] = datetime.now(timezone.utc).isoformat()

    if args.repos:
        repos = [{'name': r, 'isArchived': False, 'mergedPrCount': -1} for r in args.repos]
    else:
        logger.info("Discovering repos in %s...", args.org)
        repos = discover_repos(client, args.org, args.exclude_archived, args.exclude_repos)
        logger.info("Found %d repos", len(repos))

    repos_manifest = args.output_dir.parent / 'repos.json'
    with open(repos_manifest, 'w') as f:
        json.dump(repos, f, indent=2)

    if args.max_repos:
        repos = repos[:args.max_repos]

    completed_set = set(state['completed_repos'])
    total_prs_fetched = 0

    for i, repo in enumerate(repos, 1):
        repo_name = repo['name']
        if repo_name in completed_set:
            logger.debug("Skipping %s (already completed)", repo_name)
            continue

        logger.info("[%d/%d] Fetching %s (est. %s merged PRs)...",
                    i, len(repos), repo_name,
                    repo['mergedPrCount'] if repo['mergedPrCount'] >= 0 else '?')

        try:
            prs = fetch_prs_for_repo(client, args.org, repo_name, args.since, args.until)
        except (GraphQLError, Exception) as e:
            logger.error("Failed to fetch %s: %s", repo_name, e)
            continue

        if prs:
            output_file = args.output_dir / f"{repo_name}.jsonl"
            with open(output_file, 'w') as f:
                for pr in prs:
                    f.write(json.dumps(pr) + '\n')
            logger.info("  -> %d PRs written to %s", len(prs), output_file.name)
        else:
            logger.info("  -> 0 PRs in date range")

        total_prs_fetched += len(prs)
        state['completed_repos'].append(repo_name)
        save_fetch_state(state_file, state)

    logger.info("Done. Fetched %d total PRs from %d repos.", total_prs_fetched, len(repos))
    logger.info("Data saved to: %s", args.output_dir)


if __name__ == '__main__':
    main()
