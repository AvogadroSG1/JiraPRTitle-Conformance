import json, re, sys
from collections import defaultdict, Counter

# Jira project key pattern: uppercase letters, dash, number in title or body
JIRA_RE = re.compile(r'\b([A-Z][A-Z0-9]{1,9}-\d+)\b')

# Automation/bot signals
BOT_AUTHORS = {'dependabot', 'dependabot[bot]', 'renovate', 'renovate[bot]', 'github-actions', 'github-actions[bot]'}
BOT_TITLE_RE = re.compile(r'(bump|update|upgrade|chore\(deps\)|dependabot|renovate)', re.IGNORECASE)
CI_TITLE_RE  = re.compile(r'^(ci:|chore:)', re.IGNORECASE)
REVERT_RE    = re.compile(r'^revert\b', re.IGNORECASE)

prs = []
with open('/tmp/so-pr-analysis/raw_prs.jsonl') as f:
    for line in f:
        line = line.strip()
        if line:
            prs.append(json.loads(line))

total = len(prs)
no_jira = []
with_jira = []

for pr in prs:
    title = pr.get('title') or ''
    body  = pr.get('body')  or ''
    combined = title + ' ' + body
    matches = JIRA_RE.findall(combined)
    if matches:
        pr['jira_keys'] = list(set(matches))
        with_jira.append(pr)
    else:
        pr['jira_keys'] = []
        no_jira.append(pr)

print(f"Total merged PRs:  {total}")
print(f"With Jira link:    {len(with_jira)} ({100*len(with_jira)/total:.1f}%)")
print(f"Without Jira link: {len(no_jira)} ({100*len(no_jira)/total:.1f}%)")

# Classify no-jira PRs
def classify(pr):
    title  = pr.get('title') or ''
    author = pr.get('author') or ''
    labels = pr.get('labels') or []

    if author.lower() in BOT_AUTHORS or any(l.lower() in ('dependencies','automerge') for l in labels):
        return 'bot/automated-dependency'
    if BOT_TITLE_RE.search(title):
        return 'bot/automated-dependency'
    if REVERT_RE.match(title):
        return 'revert'
    if CI_TITLE_RE.match(title):
        return 'ci/chore'
    # Size heuristic on title keywords
    title_lower = title.lower()
    if any(k in title_lower for k in ['fix','bug','hotfix','patch']):
        return 'fix/hotfix'
    if any(k in title_lower for k in ['merge','sync','cherry-pick','cherry pick']):
        return 'merge/sync'
    if any(k in title_lower for k in ['release','deploy','bump version']):
        return 'release/deploy'
    if any(k in title_lower for k in ['cleanup','clean up','refactor','lint','format','typo','nit','style']):
        return 'cleanup/refactor'
    if any(k in title_lower for k in ['test','spec','coverage']):
        return 'test'
    if any(k in title_lower for k in ['doc','readme','changelog','comment']):
        return 'docs'
    if 'wip' in title_lower or 'draft' in title_lower:
        return 'wip/draft'
    return 'feature/other-unlabeled'

category_counts = Counter()
category_examples = defaultdict(list)

for pr in no_jira:
    cat = classify(pr)
    pr['category'] = cat
    category_counts[cat] += 1
    if len(category_examples[cat]) < 5:
        category_examples[cat].append(pr)

print("\n--- No-Jira PR Categories ---")
for cat, count in category_counts.most_common():
    print(f"  {cat:<35} {count:>4}  ({100*count/len(no_jira):.1f}%)")

# Author breakdown for no-jira
author_counts = Counter(pr['author'] for pr in no_jira)
print("\n--- Top 15 authors of no-Jira PRs ---")
for author, count in author_counts.most_common(15):
    print(f"  {author:<30} {count}")

# Reverts
reverts = [pr for pr in no_jira if pr['category'] == 'revert']
print(f"\n--- Reverts ({len(reverts)}) ---")
for pr in reverts[:10]:
    print(f"  #{pr['number']} {pr['mergedAt'][:10]}  {pr['title'][:90]}")

# Save classified no-jira for inspection
with open('/tmp/so-pr-analysis/no_jira.json', 'w') as f:
    json.dump(no_jira, f, indent=2)

print("\n--- Examples per category (first 3 each) ---")
for cat, count in category_counts.most_common():
    print(f"\n[{cat}] ({count} total)")
    for pr in category_examples[cat][:3]:
        body_snip = (pr.get('body') or '')[:80].replace('\n',' ')
        print(f"  #{pr['number']} by {pr['author']}: {pr['title'][:70]}")
        print(f"    body: {body_snip}")
