import json, re
from collections import defaultdict, Counter

with open('/tmp/so-pr-analysis/no_jira.json') as f:
    no_jira = json.load(f)

BOT_AUTHORS = {'dependabot', 'dependabot[bot]', 'renovate', 'renovate[bot]'}
BOT_TITLE_RE = re.compile(r'(bump|update|upgrade|chore\(deps\)|chore\(dev-deps\))', re.IGNORECASE)
NO_JIRA_EXPLICIT_RE = re.compile(r'NO[_\-]?JIRA|no[_\-]?jira|no ticket|no issue', re.IGNORECASE)

# ---- 1. "Feature/other-unlabeled" deep dive: look for patterns ----
unlabeled = [pr for pr in no_jira if pr['category'] == 'feature/other-unlabeled']

# Sub-classify unlabeled
def sub_classify(pr):
    title = (pr.get('title') or '').lower()
    body  = (pr.get('body')  or '').lower()
    combined = title + ' ' + body
    # Explicit "no jira" declarations
    if NO_JIRA_EXPLICIT_RE.search(pr.get('title','') + ' ' + (pr.get('body') or '')):
        return 'explicitly-no-jira'
    if any(k in title for k in ['enable','disable','toggle','flag','feature flag','experiment','ab test','rollout']):
        return 'feature-flag/experiment'
    if any(k in title for k in ['add','implement','support','introduce','initial','first','new ']):
        return 'new-feature'
    if any(k in title for k in ['update','upgrade','bump','change','adjust','tweak','modify']):
        return 'update/change'
    if any(k in title for k in ['remove','delete','drop','deprecate']):
        return 'removal/deprecation'
    if any(k in title for k in ['csp','security','nonce','cors']):
        return 'security-hardening'
    if 'wip' in title:
        return 'wip'
    if 'merge' in title or 'cherry' in title:
        return 'merge/sync'
    return 'uncategorized'

sub_counts = Counter()
sub_examples = defaultdict(list)
for pr in unlabeled:
    sc = sub_classify(pr)
    sub_counts[sc] += 1
    if len(sub_examples[sc]) < 4:
        sub_examples[sc].append(pr)

print("=== Sub-classification of 'feature/other-unlabeled' (179 PRs) ===")
for sc, count in sub_counts.most_common():
    print(f"  {sc:<30} {count:>4}")
    for pr in sub_examples[sc][:2]:
        print(f"      #{pr['number']} {pr['title'][:75]}")

# ---- 2. Bot / automated breakdown ----
bots = [pr for pr in no_jira if pr['category'] == 'bot/automated-dependency']
bot_authors = Counter(pr['author'] for pr in bots)
print(f"\n=== Bot/Automated ({len(bots)} PRs) - author breakdown ===")
for author, count in bot_authors.most_common():
    print(f"  {author:<30} {count}")

# Show manual "stacks update" pattern (so-tooling or chore(dev-deps))
stacks_updates = [pr for pr in bots if 'stacks' in (pr.get('title') or '').lower()]
print(f"\n  Stacks design system updates: {len(stacks_updates)}")
for pr in stacks_updates[:5]:
    print(f"    #{pr['number']} by {pr['author']}: {pr['title'][:70]}")

# ---- 3. so-tooling top contributor ----
so_tooling = [pr for pr in no_jira if pr['author'] == 'so-tooling']
print(f"\n=== so-tooling account ({len(so_tooling)} no-Jira PRs) ===")
titles = Counter(re.sub(r'\[.*?\]','[...]', pr['title'][:60]) for pr in so_tooling)
for t, count in titles.most_common(10):
    print(f"  {count:>3}x  {t}")

# ---- 4. Explicit NO-JIRA tagging ----
explicit_nojira = [pr for pr in no_jira if NO_JIRA_EXPLICIT_RE.search((pr.get('title') or '') + ' ' + (pr.get('body') or ''))]
print(f"\n=== Explicitly tagged NO-JIRA ({len(explicit_nojira)} PRs) ===")
for pr in explicit_nojira[:10]:
    print(f"  #{pr['number']} by {pr['author']}: {pr['title'][:80]}")

# ---- 5. Fix/hotfix without Jira ----
fixes = [pr for pr in no_jira if pr['category'] == 'fix/hotfix']
fix_authors = Counter(pr['author'] for pr in fixes)
print(f"\n=== Fix/hotfix without Jira ({len(fixes)} PRs) - top authors ===")
for author, count in fix_authors.most_common(8):
    print(f"  {author:<30} {count}")
print("  Examples:")
for pr in fixes[:8]:
    print(f"    #{pr['number']} {pr['mergedAt'][:10]} by {pr['author']}: {pr['title'][:70]}")

# ---- 6. Quarterly trend ----
from datetime import datetime
quarters = defaultdict(lambda: {'total':0,'nojira':0})
with open('/tmp/so-pr-analysis/raw_prs.jsonl') as f:
    all_prs = [json.loads(l) for l in f if l.strip()]

def quarter(dt_str):
    dt = datetime.fromisoformat(dt_str.replace('Z','+00:00'))
    q = (dt.month - 1) // 3 + 1
    return f"{dt.year}-Q{q}"

for pr in all_prs:
    q = quarter(pr['mergedAt'])
    quarters[q]['total'] += 1

for pr in no_jira:
    q = quarter(pr['mergedAt'])
    quarters[q]['nojira'] += 1

print("\n=== No-Jira rate by quarter ===")
for q in sorted(quarters):
    t = quarters[q]['total']
    n = quarters[q]['nojira']
    bar = '#' * int(30 * n / t)
    print(f"  {q}  {n:>3}/{t:<4} = {100*n/t:4.1f}%  {bar}")

