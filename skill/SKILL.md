---
name: init-ai-cicd
description: Bootstrap the full AI CI/CD pipeline (PR review, auto-fix, release notes, issue triage, security scan, test suggestions, @coder issue implementation) into a project in one command. Detects the stack, generates .github/ai-cicd.yml + thin workflows that call ajilisiwei/ai-cicd-action, and prints a prerequisites checklist. Use when the user wants to add AI CI/CD to a new repo, "初始化 CICD", "init ai-cicd", or set up the AI workflows.
---

# init-ai-cicd

One-command bootstrap of the reusable AI CI/CD pipeline into any GitHub project.
The heavy lifting is a deterministic generator; your job is to run it, confirm the
detected config with the user, and walk them through the prerequisites.

## Steps

1. **Confirm the target repo.** Default to the current working directory. Verify it's
   a git repo with a GitHub remote (`git remote -v`).

2. **Dry-run first** to show what will be generated without touching anything:
   ```bash
   python <skill-dir>/init_ai_cicd.py --root <repo> --dry-run
   ```
   Show the user the detected `language / test command / source dir` and the file list.
   If detection is wrong (e.g. `generic`), tell them which fields in `ai-cicd.yml` to fix.

3. **Choose the mode:**
   - `--mode reference` (default): workflows call `uses: ajilisiwei/ai-cicd-action@v1`.
     Single source of truth. Requires the action repo be reachable — if the **target
     repo is public and the action repo is private, GitHub blocks it**; make the action
     repo public or use vendor mode.
   - `--mode vendor`: copies the engine into `.github/ai-cicd-engine/` (self-contained,
     no cross-repo dependency; the copy will drift from upstream).

4. **Generate:**
   ```bash
   python <skill-dir>/init_ai_cicd.py --root <repo> [--mode vendor]
   ```
   Existing files are never clobbered without `--force`. After generating, open the new
   `.github/ai-cicd.yml` and fill in `project.description` (it ships with a TODO).

5. **Walk the user through the printed prerequisites checklist**, especially:
   - Secrets: `DEEPSEEK_API_KEY` (or their provider's key), `GH_PAT` (repo scope).
   - Default-branch rule: `issue_comment` / `workflow_run` / scheduled workflows only
     run from the **default branch** — the changes must reach `main` to activate them.

6. **Validate** (optional but recommended): commit on a branch, then trigger a cheap
   smoke via `gh workflow run ai-issue-triage.yml --ref <branch>` and check the run.

## What gets generated

- `.github/ai-cicd.yml` — project manifest (name, language, commands, layout, provider,
  action switches). This is the primary customization surface. See
  `../engine/ai-cicd.example.yml` for the full schema, including the `prompts:` block for
  per-action prompt tuning.
- `.github/workflows/*.yml` — thin triggers: `ci`, `ai-pr-review`, `ai-test-suggestion`,
  `ai-issue-triage`, `release-notes`, `ai-auto-fix`, `ai-implement-issue`, and
  `ai-security-scan` (Node only — the audit path assumes npm).

## Language support

Node is fully supported (reference implementation). Python and Go get correct config +
runtime setup for the test-gate; their deep codegen gates (import validation) fall back
to the generic profile until language profiles land. `security_triage` is Node-only for
now (npm-audit shaped) and is auto-disabled for other languages.

## Pitfalls

Read `references/pitfalls.md` before debugging a failing run — it captures the
hard-won operational traps (default-branch triggers, PAT vs github.token, self-trigger
loops, `${{ }}` injection). Most first-run failures are in that table.
