import json, re
from collections import Counter

with open('/tmp/so-pr-analysis/no_jira.json') as f:
    no_jira = json.load(f)

# Size proxy: use additions+deletions from the search API (not available here — but title length and
# body richness can hint). Let's pull PR sizes for a sample via gh CLI for the interesting categories.
# Instead, check body length as a rough proxy.

fixes    = [pr for pr in no_jira if pr['category'] == 'fix/hotfix']
unlabeled= [pr for pr in no_jira if pr['category'] == 'feature/other-unlabeled']

print("=== Body-length proxy for fix/hotfix (empty body = likely small) ===")
empty_body_fixes = [pr for pr in fixes if not (pr.get('body') or '').strip() or (pr.get('body') or '').strip() in ['', '## The history', '## Summary  *Your summary.*  ## How to Test  1. *Steps to test*  -------']]
print(f"  Fixes with essentially empty body: {len(empty_body_fixes)}/{len(fixes)}")

# Look for marradoss patterns (top offender for fixes)
marradoss_fixes = [pr for pr in fixes if pr['author'] == 'marradoss']
print(f"\n=== marradoss fix PRs without Jira ({len(marradoss_fixes)}) ===")
for pr in marradoss_fixes:
    body_snip = (pr.get('body') or '').replace('\n',' ')[:60]
    print(f"  #{pr['number']} {pr['mergedAt'][:10]}: {pr['title'][:60]}")

# Security-related no-jira
security_keywords = re.compile(r'xss|csrf|nonce|csp|security|vuln|injection|authori[sz]|auth', re.IGNORECASE)
security_prs = [pr for pr in no_jira if security_keywords.search((pr.get('title') or '') + (pr.get('body') or ''))]
print(f"\n=== Security-adjacent no-Jira PRs ({len(security_prs)}) ===")
for pr in security_prs[:10]:
    print(f"  #{pr['number']} {pr['mergedAt'][:10]} by {pr['author']}: {pr['title'][:70]}")

