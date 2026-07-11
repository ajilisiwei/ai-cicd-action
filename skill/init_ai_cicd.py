#!/usr/bin/env python3
"""Bootstrap AI CI/CD into a project: detect the stack, generate .github/ai-cicd.yml
and the thin workflow files that call ajilisiwei/ai-cicd-action, and print a
prerequisites checklist.

Deterministic and idempotent: existing files are left untouched unless --force.

Usage (from the target repo root):
    python init_ai_cicd.py [--mode reference|vendor] [--force] [--dry-run]
                           [--action-ref OWNER/REPO@REF] [--root DIR]
"""

import argparse
import json
import os
import shutil
import sys

ACTION_REF = "ajilisiwei/ai-cicd-action@v1"

# Pinned third-party action SHAs (match the validated t-cli workflows).
SHA_CHECKOUT = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4"
SHA_SETUP_NODE = "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4"
SHA_SETUP_PYTHON = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5"
SHA_SETUP_GO = "actions/setup-go@0aaccfd150d50ccaeb58ebd88d36e91967a5f35b # v5"
SHA_GH_SCRIPT = "actions/github-script@f28e40c7f34bde8b3046d885e986cb6290c5673b # v7"
SHA_GH_RELEASE = "softprops/action-gh-release@3bb12739c298aeb8a4eeaf626c5b8d85266b0e65 # v2"
SHA_CREATE_PR = "peter-evans/create-pull-request@c5a7806660adbe173f04e3e038b0ccdcd758773c # v6"


# ======================= detection =======================

def _first_existing(root, names, default):
    for n in names:
        if os.path.exists(os.path.join(root, n)):
            return n
    return default


def _has_ext(root, subdir, ext):
    base = os.path.join(root, subdir)
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git") and not d.startswith("_")]
        if any(f.endswith(ext) for f in files):
            return True
    return False


def detect_project(root: str) -> dict:
    """Inspect the repo and return a config dict driving generation."""
    name = os.path.basename(os.path.abspath(root))

    # Node
    if os.path.exists(os.path.join(root, "package.json")):
        try:
            with open(os.path.join(root, "package.json"), encoding="utf-8") as f:
                pkg = json.load(f)
        except (OSError, json.JSONDecodeError):
            pkg = {}
        scripts = pkg.get("scripts", {}) or {}
        src = _first_existing(root, ["src", "lib", "source"], "src")
        is_ts = os.path.exists(os.path.join(root, "tsconfig.json")) or _has_ext(root, src, ".ts")
        return {
            "name": pkg.get("name") or name,
            "language": "node",
            "test": scripts.get("test") and "npm test" or "npm test",
            "build": "npm run build" if scripts.get("build") else "",
            "syntax_check": "" if is_ts else "node --check {file}",
            "audit": "npm audit --json",
            "source_dir": src,
            "test_dir": _first_existing(root, ["test", "tests", "__tests__"], "test"),
            "file_ext": [".ts"] if is_ts else [".js"],
            "security_scan": True,
            "setup": _NODE_SETUP,
        }

    # Python
    if any(os.path.exists(os.path.join(root, f))
           for f in ("pyproject.toml", "setup.py", "requirements.txt")):
        src = _first_existing(root, ["src", name.replace("-", "_")], "src")
        return {
            "name": name,
            "language": "python",
            "test": "pytest",
            "build": "",
            "syntax_check": "python -m py_compile {file}",
            "audit": "",
            "source_dir": src,
            "test_dir": _first_existing(root, ["tests", "test"], "tests"),
            "file_ext": [".py"],
            "security_scan": False,
            "setup": _PYTHON_SETUP,
        }

    # Go
    if os.path.exists(os.path.join(root, "go.mod")):
        return {
            "name": name,
            "language": "go",
            "test": "go test ./...",
            "build": "go build ./...",
            "syntax_check": "",
            "audit": "",
            "source_dir": ".",
            "test_dir": ".",
            "file_ext": [".go"],
            "security_scan": False,
            "setup": _GO_SETUP,
        }

    # Generic fallback
    return {
        "name": name,
        "language": "generic",
        "test": "",
        "build": "",
        "syntax_check": "",
        "audit": "",
        "source_dir": "src",
        "test_dir": "test",
        "file_ext": [],
        "security_scan": False,
        "setup": "",
    }


# ======================= runtime setup blocks =======================

_NODE_SETUP = f"""      - uses: {SHA_SETUP_NODE}
        with:
          node-version: "20"
          cache: npm

      - name: Install deps
        run: npm ci
"""

_PYTHON_SETUP = f"""      - uses: {SHA_SETUP_PYTHON}
        with:
          python-version: "3.11"

      - name: Install deps
        run: pip install -e . 2>/dev/null || pip install -r requirements.txt || true
"""

_GO_SETUP = f"""      - uses: {SHA_SETUP_GO}
        with:
          go-version: "stable"

      - name: Install deps
        run: go mod download
"""


# ======================= config rendering =======================

def render_config(d: dict) -> str:
    exts = ", ".join(f'"{e}"' for e in d["file_ext"])
    lines = [
        "# AI CI/CD engine config. Consumed by ajilisiwei/ai-cicd-action.",
        "# Regenerate/inspect the schema: ai-cicd-action/engine/ai-cicd.example.yml",
        "",
        "project:",
        f"  name: {d['name']}",
        f"  description: \"TODO: one-line description injected into every AI prompt\"",
        "  conventions_file: CLAUDE.md",
        "",
        f"language: {d['language']}",
        "",
        "commands:",
        f"  test: \"{d['test']}\"",
    ]
    if d["build"]:
        lines.append(f"  build: \"{d['build']}\"")
    if d["syntax_check"]:
        lines.append(f"  syntax_check: \"{d['syntax_check']}\"")
    if d["audit"]:
        lines.append(f"  audit: \"{d['audit']}\"")
    lines += [
        "",
        "layout:",
        f"  source_dir: {d['source_dir']}",
        f"  test_dir: {d['test_dir']}",
        f"  file_ext: [{exts}]",
        "",
        "providers:",
        "  default: deepseek",
        "  model: deepseek-chat",
        "",
        "engine:",
        "  backend: api",
        "",
        "actions:",
    ]
    for a in ("pr_review", "test_suggestion", "issue_triage", "auto_fix",
              "implement_issue", "changelog"):
        lines.append(f"  {a}: true")
    lines.append(f"  security_triage: {'true' if d['security_scan'] else 'false'}")
    return "\n".join(lines) + "\n"


# ======================= workflow rendering =======================

def _ai_step(name, action, ref, extra_inputs=None, indent_id=True):
    """One workflow step invoking the reusable action."""
    lines = [f"      - name: {name}"]
    if indent_id:
        lines.append("        id: ai")
    lines += [
        f"        uses: {ref}",
        "        with:",
        f"          action: {action}",
        "          llm-provider: ${{ vars.LLM_PROVIDER || 'deepseek' }}",
        "          llm-model: ${{ vars.LLM_MODEL || 'deepseek-chat' }}",
        "          llm-api-key: ${{ secrets.DEEPSEEK_API_KEY }}",
    ]
    for k, v in (extra_inputs or {}).items():
        lines.append(f"          {k}: {v}")
    return "\n".join(lines) + "\n"


def render_workflows(d: dict, ref: str) -> dict:
    setup = d["setup"]
    wf = {}

    # — ci.yml —
    ci_steps = f"      - uses: {SHA_CHECKOUT}\n\n{setup}"
    if d["test"]:
        ci_steps += f"\n      - name: Test\n        run: {d['test']}\n"
    else:
        ci_steps += "\n      - name: Test\n        run: echo 'TODO: set commands.test in .github/ai-cicd.yml'\n"
    wf["ci.yml"] = (
        "name: CI\n\non:\n  push:\n    branches: [main, dev]\n  pull_request:\n"
        "    branches: [main, dev]\n\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 10\n\n    steps:\n" + ci_steps
    )

    # — pr-review (no deps) —
    wf["ai-pr-review.yml"] = (
        "name: AI PR Review\n\non:\n  pull_request:\n    types: [opened, synchronize]\n\n"
        "concurrency:\n  group: ai-review-${{ github.ref }}\n  cancel-in-progress: true\n\n"
        "permissions:\n  contents: read\n  pull-requests: write\n  issues: write\n\n"
        "jobs:\n  review:\n    runs-on: ubuntu-latest\n    timeout-minutes: 10\n\n    steps:\n"
        f"      - uses: {SHA_CHECKOUT}\n        with:\n          fetch-depth: 0\n\n"
        + _ai_step("AI Code Review", "review", ref)
        + "\n      - name: Post Review Comment\n"
        f"        uses: {SHA_GH_SCRIPT}\n        with:\n          script: |\n"
        "            const fs = require('fs');\n"
        "            const review = fs.readFileSync('review.md', 'utf8').trim();\n"
        "            if (!review || review === 'No diff to review.') return;\n"
        "            await github.rest.issues.createComment({ ...context.repo,\n"
        "              issue_number: context.issue.number, body: review });\n"
    )

    # — test-suggestion (no deps) —
    wf["ai-test-suggestion.yml"] = (
        "name: AI Test Suggestion\n\non:\n  pull_request:\n    branches: [main, dev]\n"
        "    types: [opened, synchronize]\n\npermissions:\n  pull-requests: write\n\n"
        "concurrency:\n  group: test-suggestion-${{ github.ref }}\n  cancel-in-progress: true\n\n"
        "jobs:\n  suggest:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n\n    steps:\n"
        f"      - uses: {SHA_CHECKOUT}\n        with:\n          fetch-depth: 0\n\n"
        + _ai_step("AI Test Suggestion", "test_suggestion", ref)
        + "\n      - name: Post Comment\n"
        f"        uses: {SHA_GH_SCRIPT}\n        with:\n          script: |\n"
        "            const fs = require('fs');\n            let body = '';\n"
        "            try { body = fs.readFileSync('suggestion.txt', 'utf8').trim(); } catch (e) {}\n"
        "            if (!body || body.includes('No code changes')) return;\n"
        "            await github.rest.issues.createComment({ ...context.repo,\n"
        "              issue_number: context.issue.number,\n"
        "              body: `## 🧪 AI Test Suggestions\\n\\n${body}` });\n"
    )

    # — issue-triage (no deps) —
    wf["ai-issue-triage.yml"] = (
        "name: AI Issue Triage\n\non:\n  schedule:\n    - cron: \"0 7 * * 1\"\n  issues:\n"
        "    types: [opened, reopened]\n  workflow_dispatch:\n\npermissions:\n  issues: write\n\n"
        "concurrency:\n  group: issue-triage\n  cancel-in-progress: false\n\n"
        "jobs:\n  triage:\n    runs-on: ubuntu-latest\n    timeout-minutes: 10\n\n    steps:\n"
        f"      - uses: {SHA_CHECKOUT}\n\n"
        + _ai_step("AI Issue Triage", "issue_triage", ref,
                   {"github-token": "${{ github.token }}"})
        + "\n      - name: Print report\n        env:\n          REPORT: ${{ steps.ai.outputs.report }}\n"
        "        run: echo \"$REPORT\"\n"
    )

    # — release-notes (no deps) —
    wf["release-notes.yml"] = (
        "name: AI Release Notes\n\non:\n  push:\n    tags:\n      - 'v*'\n  workflow_dispatch:\n"
        "    inputs:\n      tag:\n        description: \"Tag (optional)\"\n        required: false\n"
        "        type: string\n\npermissions:\n  contents: write\n\n"
        "concurrency:\n  group: release-notes-${{ github.ref }}\n  cancel-in-progress: true\n\n"
        "jobs:\n  changelog:\n    runs-on: ubuntu-latest\n    timeout-minutes: 10\n\n    steps:\n"
        f"      - uses: {SHA_CHECKOUT}\n        with:\n          fetch-depth: 0\n\n"
        + _ai_step("Generate Release Notes", "changelog", ref,
                   {"prev-tag": "${{ github.event.inputs.tag || '' }}"}, indent_id=False)
        + "\n      - name: Check output\n        id: check\n        run: |\n"
        "          if [ ! -s RELEASE_NOTES.md ] || grep -q \"No new commits\" RELEASE_NOTES.md; then\n"
        "            echo \"skip=true\" >> $GITHUB_OUTPUT\n          else\n"
        "            echo \"skip=false\" >> $GITHUB_OUTPUT\n          fi\n\n"
        "      - name: Create GitHub Release\n        if: steps.check.outputs.skip != 'true'\n"
        f"        uses: {SHA_GH_RELEASE}\n        with:\n          body_path: RELEASE_NOTES.md\n"
        "          generate_release_notes: false\n"
    )

    # — auto-fix (needs runtime for the test gate) —
    wf["ai-auto-fix.yml"] = (
        "name: AI Auto-Fix\n\non:\n  workflow_run:\n    workflows: [\"CI\"]\n    types: [completed]\n\n"
        "permissions:\n  contents: write\n\n"
        "concurrency:\n  group: auto-fix-${{ github.event.workflow_run.head_branch }}\n"
        "  cancel-in-progress: true\n\njobs:\n  auto-fix:\n    if: >\n"
        "      github.event.workflow_run.conclusion == 'failure'\n"
        "      && github.event.workflow_run.head_branch != 'main'\n"
        "    runs-on: ubuntu-latest\n    timeout-minutes: 10\n\n    steps:\n"
        f"      - uses: {SHA_CHECKOUT}\n        with:\n"
        "          ref: ${{ github.event.workflow_run.head_branch }}\n          fetch-depth: 0\n"
        "          token: ${{ secrets.GH_PAT || github.token }}\n\n"
        + setup + "\n"
        + _ai_step("AI Auto-Fix", "auto_fix", ref, {
            "failed-run-id": "${{ github.event.workflow_run.id }}",
            "fix-branch": "${{ github.event.workflow_run.head_branch }}",
            "github-token": "${{ github.token }}",
        })
    )

    # — implement-issue (needs runtime for the test gate) —
    wf["ai-implement-issue.yml"] = (
        "name: AI Issue Implementer\n\non:\n  issue_comment:\n    types: [created]\n\n"
        "permissions:\n  contents: write\n  pull-requests: write\n  issues: write\n\n"
        "concurrency:\n  group: ai-implement-${{ github.event.issue.number }}\n"
        "  cancel-in-progress: false\n\njobs:\n  implement:\n    if: >\n"
        "      contains(github.event.comment.body, '@coder')\n"
        "      && !github.event.issue.pull_request\n"
        "      && !startsWith(github.event.comment.body, '🤖')\n"
        "      && contains(fromJSON('[\"OWNER\", \"MEMBER\", \"COLLABORATOR\"]'), github.event.comment.author_association)\n"
        "    runs-on: ubuntu-latest\n    timeout-minutes: 20\n\n    steps:\n"
        f"      - uses: {SHA_CHECKOUT}\n        with:\n          fetch-depth: 0\n"
        "          token: ${{ secrets.GH_PAT || github.token }}\n\n"
        + setup + "\n"
        + _ai_step("AI Implement", "implement_issue", ref, {
            "issue-number": "${{ github.event.issue.number }}",
            "github-token": "${{ secrets.GH_PAT || github.token }}",
        })
        + "\n      - name: Push branch\n        if: steps.ai.outputs.branch != ''\n"
        "        env:\n          BRANCH: ${{ steps.ai.outputs.branch }}\n"
        "        run: git push origin --force \"HEAD:${BRANCH}\"\n\n"
        "      - name: Create or update PR\n        if: steps.ai.outputs.branch != ''\n"
        "        env:\n          GH_TOKEN: ${{ secrets.GH_PAT }}\n"
        "          BRANCH: ${{ steps.ai.outputs.branch }}\n"
        "          PR_TITLE: ${{ steps.ai.outputs.pr_title }}\n"
        "          PR_BODY_PATH: ${{ steps.ai.outputs.pr_body_path }}\n"
        "          ISSUE_NUMBER: ${{ github.event.issue.number }}\n"
        "          VERIFIED: ${{ steps.ai.outputs.verified }}\n        run: |\n"
        "          OWNER=\"${GITHUB_REPOSITORY%/*}\"\n          set +e\n"
        "          PR_URL=$(gh pr create --base main --head \"$BRANCH\" \\\n"
        "            --title \"$PR_TITLE\" --body-file \"$PR_BODY_PATH\" --draft 2>create_err.txt)\n"
        "          rc=$?\n          set -e\n          if [ \"$rc\" -ne 0 ]; then\n"
        "            NUM=$(gh api \"repos/${GITHUB_REPOSITORY}/pulls?head=${OWNER}:${BRANCH}&state=open\" --jq '.[0].number')\n"
        "            [ -n \"$NUM\" ] && gh api -X PATCH \"repos/${GITHUB_REPOSITORY}/pulls/${NUM}\" \\\n"
        "              -f title=\"$PR_TITLE\" -f body=\"$(cat \"$PR_BODY_PATH\")\" >/dev/null\n          fi\n"
        "          # NB: bot comment must not contain '@coder' (would re-trigger this workflow).\n"
        "          gh issue comment \"$ISSUE_NUMBER\" --body \"🤖 Coder bot implemented this issue (verified=${VERIFIED}). Review the Draft PR.\"\n"
    )

    # — security-scan (node only: npm audit shape) —
    if d["security_scan"]:
        wf["ai-security-scan.yml"] = (
            "name: AI Security Scan\n\non:\n  schedule:\n    - cron: \"0 6 * * 1\"\n"
            "  workflow_dispatch:\n\npermissions:\n  contents: write\n  pull-requests: write\n\n"
            "concurrency:\n  group: security-scan\n  cancel-in-progress: false\n\n"
            "jobs:\n  scan:\n    runs-on: ubuntu-latest\n    timeout-minutes: 10\n\n    steps:\n"
            f"      - uses: {SHA_CHECKOUT}\n\n" + setup + "\n"
            "      - name: npm audit\n        continue-on-error: true\n"
            "        run: npm audit --json > audit.json 2>/dev/null || true\n\n"
            + _ai_step("AI Triage", "security_triage", ref, {"audit-file": "audit.json"})
            + "\n      - name: Apply npm audit fix\n"
            "        if: steps.ai.outputs.has_critical == 'true'\n"
            "        run: npm audit fix --package-lock-only 2>&1 || true\n\n"
            "      - name: Create Fix PR\n        if: steps.ai.outputs.has_critical == 'true'\n"
            f"        uses: {SHA_CREATE_PR}\n        with:\n          token: ${{{{ github.token }}}}\n"
            "          title: \"fix(deps): auto-fix critical security vulnerabilities\"\n"
            "          body-path: security-fix-body.md\n          branch: ai/security-fix\n"
            "          commit-message: \"fix(deps): auto-fix critical security vulnerabilities [AI]\"\n"
            "          delete-branch: true\n"
        )
    return wf


# ======================= checklist =======================

def checklist(d: dict, mode: str) -> str:
    lines = [
        "## Prerequisites — do these before the workflows can run",
        "",
        "1. Secrets (repo → Settings → Secrets and variables → Actions):",
        "   - DEEPSEEK_API_KEY   (or the key for your chosen provider)",
        "   - GH_PAT             (repo scope) — required for auto-fix push + @coder PR creation",
        "2. Variables (optional, to switch model): LLM_PROVIDER, LLM_MODEL",
        "3. Default branch: issue_comment / workflow_run / scheduled workflows only use the",
        "   DEFAULT branch's workflow files — merge these to your default branch to activate them.",
    ]
    if mode == "reference":
        lines.append(
            "4. If this repo is PUBLIC and the action repo is PRIVATE, GitHub blocks the reference. "
            "Make the action repo public, or use --mode=vendor."
        )
    if not d["test"]:
        lines.append("5. commands.test is empty in ai-cicd.yml — set it, or auto-fix/implement gates are skipped.")
    if d["language"] == "generic":
        lines.append("6. Language not auto-detected (generic profile): review ai-cicd.yml commands/layout.")
    return "\n".join(lines) + "\n"


# ======================= main =======================

def _write(path, content, force, dry, log):
    exists = os.path.exists(path)
    if exists and not force:
        with open(path, encoding="utf-8") as f:
            same = f.read() == content
        log.append(("unchanged" if same else "skipped (exists)", path))
        return
    if dry:
        log.append(("would write", path))
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    log.append(("written", path))


def generate(root, mode="reference", ref=ACTION_REF, force=False, dry=False):
    """Detect + generate. Returns (detected, log). Pure enough to unit-test."""
    d = detect_project(root)
    log = []
    gh = os.path.join(root, ".github")

    _write(os.path.join(gh, "ai-cicd.yml"), render_config(d), force, dry, log)
    for fname, content in render_workflows(d, ref).items():
        _write(os.path.join(gh, "workflows", fname), content, force, dry, log)

    if mode == "vendor":
        engine_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine")
        dest = os.path.join(gh, "ai-cicd-engine")
        if os.path.isdir(engine_src) and not dry:
            os.makedirs(dest, exist_ok=True)
            for f in os.listdir(engine_src):
                if f.endswith(".py") and f != "test_engine.py":
                    shutil.copy2(os.path.join(engine_src, f), os.path.join(dest, f))
            log.append(("vendored engine →", dest))
        else:
            log.append(("vendor (dry/no-engine)", dest))
    return d, log


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bootstrap AI CI/CD into a project.")
    ap.add_argument("--mode", choices=["reference", "vendor"], default="reference")
    ap.add_argument("--action-ref", default=ACTION_REF)
    ap.add_argument("--root", default=".")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    d, log = generate(args.root, args.mode, args.action_ref, args.force, args.dry_run)
    print(f"Detected: language={d['language']} name={d['name']} "
          f"src={d['source_dir']} test={d['test']!r}\n")
    for status, path in log:
        print(f"  [{status}] {os.path.relpath(path, args.root)}")
    print()
    print(checklist(d, args.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
