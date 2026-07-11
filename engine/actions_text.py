# actions_text.py — analysis/label actions that only READ (no code changes).
#   review, test_suggestion, changelog, summary, security_triage, issue_triage
#
# Every t-cli-specific string is now derived from ProjectConfig (cfg).

import json
import os
import re
import subprocess

from gitutil import get_diff, get_pr_context
from llm import call_llm
from report import notice, write_output
from security import INJECTION_GUARD, fenced


def review_action(cfg, profile):
    """Review the PR diff."""
    diff = get_diff(cfg)
    if not diff.strip():
        write_output("review_body", "No diff to review.")
        return

    ctx = get_pr_context()
    review = call_llm(
        system=cfg.system_body(
            "review",
            f"You are a senior engineer doing code review for {cfg.project_line()}. "
            "Be critical and specific. For each issue, state: "
            "FILE:LINE | SEVERITY (critical/major/minor) | DESCRIPTION | SUGGESTION\n\n"
            "Focus on:\n"
            "1. Logic errors and edge cases\n"
            "2. Security vulnerabilities (XSS, injection, secret leak)\n"
            "3. Error handling gaps\n"
            "4. Code style consistency with the codebase\n"
            "5. Performance issues\n\n"
            "If no issues, say 'No issues found.'"
        ) + INJECTION_GUARD,
        user=f"PR #{ctx['pr_num']} in {ctx['repo']}\n\n" + fenced("diff", diff),
    )
    write_output("review_body", review)
    with open("review.md", "w", encoding="utf-8") as f:
        f.write(review)


def test_suggestion_action(cfg, profile):
    """Suggest unit tests for new/modified code in a PR diff."""
    diff = get_diff(cfg)
    if not diff.strip():
        write_output("suggestion", "No code changes to analyze.")
        return

    test_hint = f"Tests are run via `{cfg.test_cmd}`." if cfg.test_cmd else ""
    suggestion = call_llm(
        system=cfg.system_body(
            "test_suggestion",
            f"You are a testing expert reviewing a PR for {cfg.project_line()}. {test_hint}\n\n"
            "For each new or modified function/class, suggest a unit test:\n"
            "1. What to test (function name + purpose)\n"
            "2. Key edge cases to cover\n"
            "3. A concise example test snippet in the project's existing test style\n\n"
            "Focus on the CHANGED code only. If no test-worthy changes exist, "
            "say 'No test-worthy changes detected.'"
        ) + INJECTION_GUARD,
        user="PR diff:\n" + fenced("diff", diff[:8000]),
    )
    write_output("suggestion", suggestion)
    with open("suggestion.txt", "w", encoding="utf-8") as f:
        f.write(suggestion)


def changelog_action(cfg, profile):
    """Generate release notes from git log. Auto-detects the previous tag."""
    prev_tag = os.getenv("PREV_TAG")
    if not prev_tag:
        try:
            prev_tag = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0", "HEAD~1"],
                capture_output=True, text=True, check=True, timeout=10,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            prev_tag = None

    if prev_tag:
        log = subprocess.run(
            ["git", "log", f"{prev_tag}..HEAD", "--format=%h %s (%an, %ar)"],
            capture_output=True, text=True, check=True,
        ).stdout
    else:
        log = subprocess.run(
            ["git", "log", "-30", "--format=%h %s (%an, %ar)"],
            capture_output=True, text=True, check=True,
        ).stdout

    if not log.strip():
        print("No new commits since last release.")
        return

    notes = call_llm(
        system=cfg.system_body(
            "changelog",
            f"Generate release notes from git log for {cfg.project_line()}. Group commits by:\n"
            "## Features — new capabilities\n"
            "## Bug Fixes — bug fixes and error handling\n"
            "## Refactoring — code quality, architecture\n"
            "## Docs — documentation changes\n\n"
            "For each group, list commits as bullet points with the commit hash in "
            "parentheses. If a group has no commits, omit it entirely.\n"
            "Output ONLY the release notes in valid markdown."
        ) + INJECTION_GUARD,
        user=f"Previous tag: {prev_tag or '(first release)'}\n\nCommits:\n" + fenced("log", log),
    )
    # Write to a file so the composite action can surface it without redirecting
    # its own stdout; still echo for the run log.
    out_file = os.getenv("RELEASE_NOTES_FILE", "RELEASE_NOTES.md")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(notes)
    print(notes)


def summary_action(cfg, profile):
    """Summarize an issue body."""
    body = os.getenv("ISSUE_BODY", "")
    summary = call_llm(
        system=cfg.system_body(
            "summary",
            "Summarize this issue: what is the problem, where does it occur, any proposed solution.",
        ) + INJECTION_GUARD,
        user=fenced("issue", body),
    )
    print(summary)


def security_triage_action(cfg, profile):
    """Analyze npm-audit-style results, triage vulnerabilities, and write a fix-PR body.

    Note: currently assumes an npm-audit JSON shape. Non-Node ecosystems get this in
    a later phase; for now the action is a no-op when the audit file is absent."""
    audit_file = os.getenv("AUDIT_FILE", "audit.json")
    if not os.path.exists(audit_file):
        print("::error::audit file not found — did the audit step run?")
        import sys
        sys.exit(1)

    with open(audit_file, encoding="utf-8") as f:
        audit = json.load(f)

    vulnerabilities = audit.get("vulnerabilities", {})
    if not vulnerabilities:
        write_output("summary", "No vulnerabilities found.")
        return

    buckets = {"critical": [], "high": [], "moderate": [], "low": []}
    for pkg, info in vulnerabilities.items():
        sev = info.get("severity", "unknown")
        via = info.get("via", [])
        advisory_info = []
        for v in via:
            if isinstance(v, dict):
                advisory_info.append(f"{v.get('title', '')} — {v.get('cvss', {}).get('score', 'N/A')}")
            else:
                advisory_info.append(str(v))
        entry = {
            "package": pkg,
            "severity": sev,
            "range": info.get("range", ""),
            "fix_available": info.get("fixAvailable", False),
            "advisories": advisory_info[:3],
            "via_count": len(via),
        }
        buckets.get(sev, buckets["low"]).append(entry)

    critical, high, moderate, low = (buckets["critical"], buckets["high"],
                                     buckets["moderate"], buckets["low"])

    report_text = call_llm(
        system=cfg.system_body(
            "security_triage",
            f"You are a security engineer analyzing dependency audit results for "
            f"{cfg.project_line()}. Summarize the vulnerabilities concisely:\n"
            "1. How many critical/high/moderate/low\n"
            "2. For each critical/high vulnerability: what is the risk, is a fix available\n"
            "3. Recommendation: automated fix vs manual upgrade\n\n"
            "If there are NO critical or high vulnerabilities, say 'No critical or high issues.'"
        ) + INJECTION_GUARD,
        user=(
            f"Audit summary:\n"
            f"Total vulnerabilities: {audit['metadata']['vulnerabilities']['total']}\n"
            f"Critical: {len(critical)}, High: {len(high)}, "
            f"Moderate: {len(moderate)}, Low: {len(low)}\n\n"
            f"Details:\n" + fenced("audit", json.dumps({"critical": critical, "high": high}, indent=2))
        ),
    )

    has_critical = len(critical) > 0
    write_output("summary", report_text)
    write_output("has_critical", "true" if has_critical else "false")

    if has_critical:
        pr_body = [
            "## AI Security Scan — Critical Vulnerabilities Found", "",
            report_text, "", "### Packages affected",
        ]
        for c in critical:
            pr_body.append(f"- **{c['package']}** ({c['severity']}): {c['range']}")
            for adv in c["advisories"]:
                pr_body.append(f"  - {adv}")
        pr_body += ["", "---", "*Auto-generated by AI Security Scan workflow*"]
        with open("security-fix-body.md", "w", encoding="utf-8") as f:
            f.write("\n".join(pr_body))


def issue_triage_action(cfg, profile):
    """Scan open issues; AI classifies and applies labels."""
    import sys

    gh_token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not gh_token:
        print("::error::GH_TOKEN not set")
        sys.exit(1)

    NEEDED_LABELS = {
        "bug": {"color": "d73a4a", "desc": "Something isn't working"},
        "feature": {"color": "a2eeef", "desc": "New feature or request"},
        "enhancement": {"color": "a2eeef", "desc": "New feature or request"},
        "question": {"color": "d876e3", "desc": "Further information is requested"},
        "docs": {"color": "0075ca", "desc": "Documentation changes"},
        "refactor": {"color": "bfdadc", "desc": "Code refactoring"},
        "priority:critical": {"color": "b60205", "desc": "Critical priority"},
        "priority:high": {"color": "d73a4a", "desc": "High priority"},
    }
    existing_labels = set()
    result = subprocess.run(
        ["gh", "label", "list", "--limit", "50"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 0:
        for line in result.stdout.strip().split("\n"):
            name = line.split("\t")[0] if "\t" in line else (line.split()[0] if line else "")
            if name:
                existing_labels.add(name)

    for name, lc in NEEDED_LABELS.items():
        if name not in existing_labels:
            subprocess.run(
                ["gh", "label", "create", name, "--color", lc["color"], "--description", lc["desc"]],
                capture_output=True, timeout=10,
            )
            print(f"  Created label: {name}")

    TYPE_LABEL_MAP = {
        "bug": "bug", "feature": "feature", "question": "question",
        "docs": "docs", "refactor": "refactor",
    }

    result = subprocess.run(
        ["gh", "issue", "list", "--state", "open", "--limit", "20",
         "--json", "number,title,body,labels,createdAt,comments"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"::error::gh issue list failed: {result.stderr}")
        sys.exit(1)

    issues = json.loads(result.stdout)
    if not issues:
        write_output("report", "No open issues to triage.")
        return

    for issue in issues:
        labels_on_issue = [l["name"] for l in issue.get("labels", [])]
        if any(l in ("bug", "feature", "enhancement", "question", "docs") for l in labels_on_issue):
            continue

        analysis = call_llm(
            system=cfg.system_body(
                "issue_triage",
                f"Classify this GitHub issue for {cfg.project_line()}. "
                "Return ONLY a JSON object with:\n"
                "{\n"
                '  "type": "bug|feature|question|docs|refactor",\n'
                '  "priority": "critical|high|medium|low",\n'
                '  "summary": "one-line summary (max 80 chars)"\n'
                "}\n"
                "Base the type and priority on the issue content."
            ) + INJECTION_GUARD,
            user=fenced(
                "issue",
                f"#{issue['number']}: {issue['title']}\n\n{(issue['body'] or '(no description)')[:2000]}",
            ),
        )
        try:
            json_match = re.search(r"\{[^}]+\}", analysis, re.DOTALL)
            parsed = json.loads(json_match.group()) if json_match else json.loads(analysis)
            issue_type = parsed.get("type", "question")
            priority = parsed.get("priority", "medium")
            labels_to_add = [TYPE_LABEL_MAP.get(issue_type, issue_type)]
            if priority in ("critical", "high"):
                labels_to_add.append("priority:" + priority)
            subprocess.run(
                ["gh", "issue", "edit", str(issue["number"]), "--add-label", ",".join(labels_to_add)],
                capture_output=True, timeout=15,
            )
            print(f"Issue #{issue['number']}: {issue_type}/{priority} → labels: {labels_to_add}")
        except (json.JSONDecodeError, KeyError) as e:
            print(f"::warning::Issue #{issue['number']}: parse error: {e}, raw: {analysis[:200]}")

    write_output("report", f"Triaged {len(issues)} issues.")
