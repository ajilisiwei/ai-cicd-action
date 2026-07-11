# ai_agent.py — entry point. Loads project config, selects a language profile,
# dispatches on AI_ACTION, and always reports token usage on exit.
#
# Usage (in a GitHub Actions step):
#   LLM_PROVIDER=deepseek LLM_MODEL=deepseek-chat DEEPSEEK_API_KEY=sk-... \
#     AI_ACTION=review python engine/ai_agent.py
#
# Project specifics come from .github/ai-cicd.yml (see ai-cicd.example.yml).

import os
import sys

from config import load_config
from profiles import get_profile
from report import fail, report_usage
import actions_text as t
import actions_code as c

ACTIONS = {
    "review":          t.review_action,
    "test_suggestion": t.test_suggestion_action,
    "changelog":       t.changelog_action,
    "summary":         t.summary_action,
    "security_triage": t.security_triage_action,
    "issue_triage":    t.issue_triage_action,
    "auto_fix":        c.auto_fix_action,
    "implement_issue": c.implement_issue_action,
}

# AI_ACTION → the ai-cicd.yml switch that gates it.
_ACTION_SWITCH = {
    "review": "pr_review",
    "test_suggestion": "test_suggestion",
    "changelog": "changelog",
    "security_triage": "security_triage",
    "issue_triage": "issue_triage",
    "auto_fix": "auto_fix",
    "implement_issue": "implement_issue",
}


def main():
    action = os.getenv("AI_ACTION", "review")
    handler = ACTIONS.get(action)
    if not handler:
        fail(f"Unknown action: {action}")

    cfg = load_config()
    switch = _ACTION_SWITCH.get(action)
    if switch and not cfg.action_enabled(switch):
        # Disabled via ai-cicd.yml — an expected no-op, not a failure.
        print(f"::notice::Action '{action}' is disabled in ai-cicd.yml — skipping.")
        sys.exit(0)

    profile = get_profile(cfg)
    try:
        handler(cfg, profile)
    finally:
        # Always surface token usage, even when the action bailed early via sys.exit().
        report_usage()


if __name__ == "__main__":
    main()
