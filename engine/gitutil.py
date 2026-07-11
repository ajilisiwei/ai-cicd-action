# gitutil.py — git/PR helpers parameterized by ProjectConfig.

import os
import subprocess

MAX_DIFF_CHARS = 40000


def _pathspec_excludes(cfg):
    """`:!<lockfile>` pathspecs so lock-file churn never eats the diff token budget."""
    return [f":!{name}" for name in cfg.lock_excludes]


def get_diff(cfg) -> str:
    """Diff between the PR base and HEAD (falls back to working-tree diff), truncated."""
    base = os.getenv("GITHUB_BASE_REF", "main")
    excludes = _pathspec_excludes(cfg)
    try:
        diff = subprocess.run(
            ["git", "diff", f"origin/{base}...HEAD", "--", *excludes],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout
    except subprocess.CalledProcessError:
        diff = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n# ... (diff truncated)"
    return diff


def get_pr_context() -> dict:
    """Read GitHub Actions env vars for PR context."""
    return {
        "repo":   os.getenv("GITHUB_REPOSITORY", "unknown/repo"),
        "pr_num": os.getenv("GITHUB_REF_NAME", "").replace("refs/pull/", "").split("/")[0],
        "sha":    os.getenv("GITHUB_SHA", ""),
    }


def config_git_identity(name: str, email: str):
    """Set a local git identity (runners may not have one)."""
    subprocess.run(["git", "config", "user.email", email], capture_output=True)
    subprocess.run(["git", "config", "user.name", name], capture_output=True)
