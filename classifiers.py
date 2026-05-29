"""Jira ticket detection and PR classification for no-ticket PRs."""

import re

JIRA_RE = re.compile(r'\[?([A-Z][A-Z0-9]{1,9}-\d+)\]?')
ATLASSIAN_URL_RE = re.compile(
    r'stackoverflow\.atlassian\.net/browse/([A-Z][A-Z0-9]{1,9}-\d+)'
)

BOT_AUTHORS = frozenset({
    'dependabot', 'dependabot[bot]', 'renovate', 'renovate[bot]',
    'github-actions', 'github-actions[bot]', 'so-tooling',
})
BOT_TITLE_RE = re.compile(
    r'(bump|update|upgrade|chore\(deps\)|chore\(dev-deps\)|dependabot|renovate)',
    re.IGNORECASE,
)
CI_TITLE_RE = re.compile(r'^(ci:|chore:)', re.IGNORECASE)
REVERT_RE = re.compile(r'^revert\b', re.IGNORECASE)
NO_JIRA_EXPLICIT_RE = re.compile(
    r'NO[_\-]?JIRA|no[_\-]?jira|no[_\-\s]?ticket|no[_\-\s]?issue',
    re.IGNORECASE,
)


def detect_jira_tickets(title: str, body: str) -> list[str]:
    """Return deduplicated Jira ticket keys found in title or body."""
    combined = f"{title} {body}"
    tickets = set(JIRA_RE.findall(combined))
    tickets.update(ATLASSIAN_URL_RE.findall(combined))
    return sorted(tickets)


def extract_jira_projects(tickets: list[str]) -> list[str]:
    """Extract unique project prefixes from ticket keys."""
    return sorted({t.split('-')[0] for t in tickets})


def classify_no_jira_pr(pr: dict) -> str:
    """Classify a PR that has no Jira ticket into a category."""
    title = pr.get('title') or ''
    body = pr.get('body') or ''
    author = pr.get('author') or ''
    labels = pr.get('labels') or []

    if author.lower() in BOT_AUTHORS or any(
        lbl.lower() in ('dependencies', 'automerge') for lbl in labels
    ):
        return 'bot/automated-dependency'
    if BOT_TITLE_RE.search(title):
        return 'bot/automated-dependency'
    if REVERT_RE.match(title):
        return 'revert'
    if CI_TITLE_RE.match(title):
        return 'ci/chore'

    if NO_JIRA_EXPLICIT_RE.search(f"{title} {body}"):
        return 'explicitly-no-jira'

    title_lower = title.lower()

    if any(k in title_lower for k in ('fix', 'bug', 'hotfix', 'patch')):
        return 'fix/hotfix'
    if any(k in title_lower for k in ('merge', 'sync', 'cherry-pick', 'cherry pick')):
        return 'merge/sync'
    if any(k in title_lower for k in ('release', 'deploy', 'bump version')):
        return 'release/deploy'
    if any(k in title_lower for k in (
        'cleanup', 'clean up', 'refactor', 'lint', 'format', 'typo', 'nit', 'style'
    )):
        return 'cleanup/refactor'
    if any(k in title_lower for k in ('test', 'spec', 'coverage')):
        return 'test'
    if any(k in title_lower for k in ('doc', 'readme', 'changelog', 'comment')):
        return 'docs'
    if any(k in title_lower for k in (
        'enable', 'disable', 'toggle', 'flag', 'feature flag', 'experiment',
        'ab test', 'rollout',
    )):
        return 'feature-flag/experiment'
    if 'wip' in title_lower or 'draft' in title_lower:
        return 'wip/draft'

    return 'feature/other-unlabeled'
