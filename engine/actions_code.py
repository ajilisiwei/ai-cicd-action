# actions_code.py — actions that WRITE code (auto_fix, implement_issue).
#
# The language-specific verification (symbol scan, import validation, syntax) is
# delegated to the active Profile; test/lock-file/source-dir specifics come from
# ProjectConfig. Framework-specific rules (e.g. "Ink owns stdin") are injected via
# the project's conventions file, not hardcoded here.

import json
import os
import re
import shlex
import subprocess
import sys

from llm import call_llm
from gitutil import config_git_identity
from report import fail, notice, report_usage, write_output
from security import INJECTION_GUARD, fenced

AUTO_FIX_COMMIT_MSG = "fix(ci): auto-fix CI failure [AI]"


def _run_tests(cfg, timeout=300):
    """Run the configured test command. Returns (ok, combined_output).
    No test command → gate is skipped (ok=True) with a note."""
    if not cfg.test_cmd:
        return True, "(no test command configured — test gate skipped)"
    try:
        r = subprocess.run(shlex.split(cfg.test_cmd), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"test command could not run: {e}"
    return r.returncode == 0, (r.stdout + r.stderr)


# ============================ auto_fix ============================

def auto_fix_action(cfg, profile):
    """Fix failing CI tests. Reads logs, AI generates a fix, verifies, commits, pushes."""
    run_id = os.getenv("FAILED_RUN_ID")
    if not run_id:
        fail("FAILED_RUN_ID not set")

    # Loop guard: once we push a fix with GH_PAT, CI re-runs; if it still fails,
    # HEAD is already our own auto-fix commit — stop instead of fixing our fix.
    head_subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"], capture_output=True, text=True, timeout=10
    ).stdout.strip()
    if head_subject == AUTO_FIX_COMMIT_MSG:
        notice("HEAD is already an AI auto-fix commit — skipping to avoid a fix loop.")
        sys.exit(0)

    excludes = [f":!{name}" for name in cfg.lock_excludes]
    diff = subprocess.run(
        ["git", "diff", "HEAD~1..HEAD", "--", *excludes],
        capture_output=True, text=True, timeout=15,
    ).stdout

    log = subprocess.run(["gh", "run", "view", run_id, "--log"],
                         capture_output=True, text=True, timeout=30).stdout
    log_lines = log.split("\n")
    error_lines = [l for l in log_lines
                   if any(k in l.lower() for k in ("error", "fail", "assert")) or "✖" in l or "×" in l]
    error_log = "\n".join(error_lines[-40:])
    if len(error_log) < 50:
        error_log = log[-5000:]

    test_hint = f"Tests use `{cfg.test_cmd}`. " if cfg.test_cmd else ""
    fix = call_llm(
        system=(
            f"You are a senior engineer fixing a CI test failure for {cfg.project_line()}. "
            f"{test_hint}\n\n"
            "Rules:\n"
            f"1. Fix the SOURCE CODE in {cfg.source_dir}/, NEVER modify test files in {cfg.test_dir}/\n"
            "2. Return ONLY the exact code change as a unified diff (git diff format)\n"
            "3. Be minimal — change only what's needed to pass the test\n"
            "4. If the issue is a missing null check / edge case, add the guard\n"
            "5. If you cannot determine the fix, output 'UNSURE: <reason>'"
            + INJECTION_GUARD
        ),
        user=(
            f"CI Test Failure (run #{run_id}):\n\n"
            f"PR Diff (what changed):\n{fenced('diff', diff[:5000])}\n\n"
            f"Error Log:\n{fenced('log', error_log[:5000])}"
        ),
    )

    if fix.startswith("UNSURE:"):
        notice(f"AI could not determine a fix: {fix}")
        report_usage()
        sys.exit(0)

    result = subprocess.run(["git", "apply", "--index"], input=fix, text=True,
                            capture_output=True, timeout=15)
    if result.returncode != 0:
        print(f"::warning::git apply failed: {result.stderr[:500]}")
        result = subprocess.run(["git", "apply", "--index", "--reject", "--whitespace=fix"],
                                input=fix, text=True, capture_output=True, timeout=15)
        if result.returncode != 0:
            notice(f"AI-generated diff did not apply cleanly, skipping: {result.stderr[:300]}")
            report_usage()
            sys.exit(0)

    config_git_identity("AI Auto-Fix Bot", "ai-auto-fix[bot]@users.noreply.github.com")

    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
    if not status.strip():
        notice("AI fix produced no changes, nothing to commit.")
        report_usage()
        sys.exit(0)

    # Verify the fix actually passes tests before pushing. A partial `git apply
    # --reject` or a wrong fix must never reach the branch.
    print("Running tests to verify the fix...")
    ok, out = _run_tests(cfg)
    if not ok:
        notice("AI fix did not pass tests — discarding, nothing pushed.")
        print(out[-2000:])
        report_usage()
        sys.exit(0)
    print("✅ Tests pass after fix.")

    branch = os.getenv("FIX_BRANCH") or os.getenv("GITHUB_HEAD_REF") or os.getenv("GITHUB_REF_NAME", "")
    subprocess.run(["git", "commit", "-m", AUTO_FIX_COMMIT_MSG], check=False)
    push_result = subprocess.run(["git", "push", "origin", f"HEAD:{branch}"],
                                 capture_output=True, text=True, timeout=30)
    if push_result.returncode == 0:
        print(f"✅ Auto-fix pushed to {branch}")
    else:
        # Push is infrastructure, not AI judgment — a failure here must not be a silent green run.
        fail(f"Push to {branch} failed: {push_result.stderr[:300]}")


# ============================ implement_issue ============================

def _apply_diff(diff_text: str) -> bool:
    """Apply a unified diff, tolerating the line-number/whitespace sloppiness typical of
    LLM-generated diffs. `git apply` without --reject is atomic, so a failed attempt
    leaves the tree untouched. Returns True on success."""
    if not diff_text.strip():
        return False
    attempts = (
        ["git", "apply", "--recount", "--whitespace=fix"],
        ["git", "apply", "--recount", "-C1", "--whitespace=fix"],
        ["git", "apply", "--recount", "--unidiff-zero", "--whitespace=fix"],
    )
    for cmd in attempts:
        r = subprocess.run(cmd, input=diff_text, text=True, capture_output=True, timeout=15)
        if r.returncode == 0:
            return True
    return False


def _verify_generated(cfg, profile, files, export_map, original_exports):
    """Gate generated code: no truncation placeholders, valid syntax, resolvable imports,
    no dropped pre-existing exports, and a passing test suite. Returns (ok, report_lines)."""
    report = []
    ok = True
    src_files = [f for f in files if profile.is_source_file(f)]

    # 1. Truncation placeholders
    for fp in src_files:
        try:
            ph = profile.find_placeholders(open(fp, encoding="utf-8").read())
        except OSError:
            ph = []
        if ph:
            ok = False
            report.append(f"❌ {fp}: truncated with placeholder comment → {ph[0]}")

    # 2. Syntax
    for fp in src_files:
        syn_ok, msg = profile.syntax_check(fp)
        if not syn_ok:
            ok = False
            report.append(f"❌ {fp}: syntax error → {msg}")

    # 3. Resolvable, real imports (no-op for profiles without static import checks)
    for e in profile.validate_imports(src_files, export_map):
        ok = False
        report.append(f"❌ {e}")

    # 4. No pre-existing export silently dropped
    for fp, orig in original_exports.items():
        now = set(export_map.get(os.path.normpath(fp), []))
        missing = set(orig) - now
        if missing:
            ok = False
            report.append(f"❌ {fp}: dropped existing export(s): {', '.join(sorted(missing))}")

    # 5. Test suite
    tests_ok, out = _run_tests(cfg)
    if not tests_ok:
        ok = False
        tail = out.strip().splitlines()[-8:]
        report.append("❌ tests failed:\n    " + "\n    ".join(tail))
    else:
        report.append("✅ tests passed")

    if ok:
        report.insert(0, "✅ placeholders, syntax, imports, exports and tests all OK")
    return ok, report


def _manifest_context() -> str:
    """Best-effort package manifest summary (currently Node package.json)."""
    if not os.path.exists("package.json"):
        return ""
    try:
        with open("package.json", encoding="utf-8") as f:
            pkg = json.load(f)
        return (f"Entry: {pkg.get('bin', {})}\n"
                f"Dependencies: {', '.join((pkg.get('dependencies') or {}).keys())}")
    except (OSError, json.JSONDecodeError):
        return ""


def implement_issue_action(cfg, profile):
    """Implement a feature described in an issue. Triggered by an @coder comment."""
    issue_number = os.getenv("ISSUE_NUMBER")
    gh_token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not issue_number or not gh_token:
        fail("ISSUE_NUMBER and GH_TOKEN required")

    # ── 1. Issue context ──
    result = subprocess.run(
        ["gh", "issue", "view", issue_number, "--json", "title,body,labels,author,number"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        fail(f"gh issue view failed: {result.stderr}")
    issue = json.loads(result.stdout)
    issue_title = issue["title"]
    issue_body = issue.get("body") or "(no description)"
    print(f"Implementing issue #{issue_number}: {issue_title}")

    # ── 2. Real project API (exact exports + signatures) so imports aren't guessed ──
    export_map, api_ref = profile.scan_symbols(cfg.source_dir)
    pkg_info = _manifest_context()
    api_block = (
        f"### Module API reference — import ONLY these exact names from these exact paths\n{api_ref}\n\n"
        if api_ref else ""
    )
    project_context = (
        api_block
        + (f"### Package\n{pkg_info}\n\n" if pkg_info else "")
        + (f"### Project conventions & architecture — follow STRICTLY\n{cfg.conventions}"
           if cfg.conventions else "")
    )

    # ── 3. Plan ──
    plan = call_llm(
        system=(
            f"You are implementing a feature for {cfg.project_line()}. Produce a JSON plan:\n\n"
            "{\n"
            '  "summary": "one-line description",\n'
            f'  "files_to_create": ["{cfg.source_dir}/utils/newfile"],\n'
            f'  "files_to_modify": ["{cfg.source_dir}/main"],\n'
            '  "approach": "concise technical approach naming the exact existing functions to reuse",\n'
            '  "test_changes": "tests to add"\n'
            "}\n\n"
            "Rules:\n"
            "- Prefer ADDING new files/functions over modifying large existing files.\n"
            "- Only list files that genuinely need changes.\n"
            f"- New files follow the project's existing layout under {cfg.source_dir}/.\n"
            "- Reuse existing exports from the API reference; never invent module names or paths.\n"
            "- Follow existing patterns and the project conventions."
            + INJECTION_GUARD
        ),
        user=(
            fenced("issue", f"#{issue_number}: {issue_title}\n\n{issue_body[:3000]}")
            + f"\n\n{project_context}"
        ),
    )
    print(f"=== AI Plan ===\n{plan}\n")

    # Parse JSON from the plan (handle nested objects and markdown-wrapped output)
    try:
        json_block = re.search(r"```(?:json)?\s*([\s\S]*?)```", plan)
        plan_str = json_block.group(1) if json_block else plan
        brace_start = plan_str.index("{")
        depth, brace_end = 0, -1
        for i in range(brace_start, len(plan_str)):
            if plan_str[i] == "{":
                depth += 1
            elif plan_str[i] == "}":
                depth -= 1
                if depth == 0:
                    brace_end = i + 1
                    break
        plan_str = plan_str[brace_start:brace_end] if brace_end > brace_start else plan_str
        parsed = json.loads(plan_str)
        files_to_create = parsed.get("files_to_create", []) or []
        files_to_modify = parsed.get("files_to_modify", []) or []
        approach = parsed.get("approach", "No approach specified")
    except (json.JSONDecodeError, AttributeError, ValueError) as e:
        print(f"::warning::Could not parse plan JSON: {e}")
        print(f"Raw plan:\n{plan}")
        files_to_create, files_to_modify = [], []
        approach = plan[:500]

    all_files = list(dict.fromkeys(files_to_create + files_to_modify))
    if not all_files:
        notice("Plan specified no files to change — nothing to implement.")
        sys.exit(0)

    original_exports = {
        fp: list(export_map.get(os.path.normpath(fp), []))
        for fp in files_to_modify if os.path.exists(fp)
    }

    codegen_system = (
        f"Generate the COMPLETE content of ONE file for {cfg.project_line()}. "
        "Output ONLY the file content in a single code block.\n\n"
        "CRITICAL rules:\n"
        "- Output EVERY line of the file. NEVER abbreviate. NEVER write placeholder comments "
        "such as '// ... rest of existing code', '// existing code continues', '// unchanged', "
        "or a bare '...'. Such placeholders CORRUPT the file and are rejected.\n"
        "- When modifying a file, reproduce the ENTIRE original content with your change applied, "
        "keeping every existing import, export, function and entry/render code intact.\n"
        "- Import ONLY real exports from the API reference, using the exact path and name.\n"
        "- OBEY the project conventions/architecture in the context — especially the UI/IO "
        "control model. Do not violate the framework's ownership of stdin/stdout or its lifecycle.\n"
        "- Match the project's existing language, module system, and style; handle null/edge cases."
        + INJECTION_GUARD
    )

    def _gen_file(filepath, existing_context, extra=""):
        raw = call_llm(
            system=codegen_system,
            user=(
                fenced("issue", f"{issue_title}\n\nApproach: {approach}")
                + f"\n\n{project_context}\n\nTarget file: {filepath}\n{existing_context}"
                + (f"\n\n{extra}" if extra else "")
            ),
        )
        m = re.search(r"```(?:[a-zA-Z]*)?\n?([\s\S]*?)```", raw)
        return (m.group(1) if m else raw).strip() + "\n"

    def _full_rewrite(filepath, existing_context):
        """Whole-file generation with an immediate retry if it comes back truncated."""
        code = _gen_file(filepath, existing_context)
        if profile.find_placeholders(code):
            print(f"::warning::{filepath}: placeholder/truncation detected — retrying once")
            code = _gen_file(
                filepath, existing_context,
                extra="Your previous output used forbidden placeholder comments and truncated the "
                      "file. Re-output the COMPLETE file with every line present and NO "
                      "ellipsis / 'rest of' / 'unchanged' comments.",
            )
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)

    def _gen_diff(filepath, existing_code):
        """Ask for a minimal unified diff for an existing file (touches only the
        lines the feature needs, so unrelated code/exports can't be dropped)."""
        raw = call_llm(
            system=(
                "Produce a MINIMAL unified diff (git format) applying the requested change to "
                "ONE existing file. Output ONLY the diff inside a single ```diff code block.\n\n"
                "Rules:\n"
                f"- Use exactly these file headers:\n  --- a/{filepath}\n  +++ b/{filepath}\n"
                "- Include a @@ hunk header and ~3 lines of unchanged context around each edit.\n"
                "- Change ONLY what the feature needs; do NOT touch or restate unrelated code.\n"
                "- Do NOT rewrite the whole file and do NOT emit placeholder comments.\n"
                "- Import only real exports from the API reference (exact name and path).\n"
                "- OBEY the project conventions/architecture in the context (UI/IO control model)."
                + INJECTION_GUARD
            ),
            user=(
                fenced("issue", f"{issue_title}\n\nApproach: {approach}")
                + f"\n\n{project_context}\n\nFile to modify: {filepath}\n"
                + f"Current content:\n```\n{existing_code}\n```"
            ),
        )
        m = re.search(r"```(?:diff|patch)?\n?([\s\S]*?)```", raw)
        return (m.group(1) if m else raw).strip() + "\n"

    # ── 4. Generate each file ──
    # New files: whole-file generation. Existing files: try a minimal diff first
    # (can't drop unrelated code), fall back to a full rewrite if it won't apply.
    changes_made = []
    for filepath in all_files:
        is_modify = filepath in files_to_modify and os.path.exists(filepath)
        if is_modify:
            with open(filepath, encoding="utf-8") as f:
                existing_code = f.read()
            diff = _gen_diff(filepath, existing_code)
            if _apply_diff(diff):
                changes_made.append(filepath)
                print(f"  ✓ {filepath} patched via diff")
                continue
            print(f"::warning::{filepath}: diff did not apply — falling back to full rewrite")
            _full_rewrite(
                filepath,
                f"Existing content (reproduce IN FULL with your change applied):\n```\n{existing_code}\n```",
            )
        else:
            _full_rewrite(filepath, "(this is a NEW file)")
        changes_made.append(filepath)
        print(f"  ✓ {filepath} {'updated' if is_modify else 'created'}")

    if not changes_made:
        notice("No files were generated.")
        sys.exit(0)

    # ── 5. Verify, with one automated repair round on failure ──
    export_map, _ = profile.scan_symbols(cfg.source_dir)  # refresh: new files now importable
    ok, report = _verify_generated(cfg, profile, changes_made, export_map, original_exports)
    if not ok:
        print("::warning::Verification failed — attempting one repair round")
        print("\n".join(report))
        broken = [f for f in changes_made if profile.is_source_file(f)
                  and any(f in line for line in report if line.startswith("❌"))]
        if not broken:
            broken = [f for f in changes_made if profile.is_source_file(f)]
        for filepath in broken:
            with open(filepath, encoding="utf-8") as f:
                current = f.read()
            repaired = _gen_file(
                filepath,
                existing_context=f"Current (BROKEN) content:\n```\n{current}\n```",
                extra="This file FAILED automated verification:\n" + "\n".join(report) +
                      "\n\nFix EVERY issue. Output the complete corrected file — only real exports "
                      "from the API reference, every line present, no placeholders.",
            )
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(repaired)
        export_map, _ = profile.scan_symbols(cfg.source_dir)
        ok, report = _verify_generated(cfg, profile, changes_made, export_map, original_exports)

    print(f"=== Verification: {'passed' if ok else 'FAILED'} ===")
    print("\n".join(report))

    # ── 6. Commit on the issue branch ──
    branch = f"ai/issue-{issue_number}"
    config_git_identity("AI Coder Bot", "ai-coder[bot]@users.noreply.github.com")
    subprocess.run(["git", "checkout", "-B", branch], capture_output=True)
    subprocess.run(["git", "add", "-A"], capture_output=True)
    commit_result = subprocess.run(
        ["git", "commit", "-m", f"feat: implement #{issue_number} - {issue_title[:60]}"],
        capture_output=True, text=True,
    )
    if commit_result.returncode != 0:
        notice(f"Nothing to commit: {commit_result.stderr[:200]}")
        sys.exit(0)
    print(f"  ✓ Changes committed locally on branch: {branch}")

    # ── 7. PR body with an honest verification report ──
    banner = ("> Automated verification PASSED - syntax, imports, exports and tests all OK.\n"
              if ok else
              "> Automated verification FAILED - do NOT merge as-is. See the report below.\n")
    pr_body = (
        f"## AI-Generated Implementation\n\n{banner}\n"
        f"Implements **{issue_title}**\n\n"
        f"### Issue\nCloses #{issue_number}\n\n"
        f"### Approach\n{approach}\n\n"
        f"### Verification\n```\n" + "\n".join(report) + "\n```\n"
        f"### Files Changed\n" + "\n".join(f"- {f}" for f in changes_made) +
        "\n\n---\n*🤖 Generated by @coder — please review before merging*"
    )
    with open("pr-body.md", "w", encoding="utf-8") as f:
        f.write(pr_body)

    print(f"Branch ready: {branch} (verified={ok})")
    write_output("branch", branch)
    write_output("pr_title", f"feat: {issue_title[:80]}")
    write_output("pr_body_path", "pr-body.md")
    write_output("verified", "true" if ok else "false")
