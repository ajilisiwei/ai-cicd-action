# report.py — GitHub Actions output, logging, and LLM token-usage accounting.
#
# Behavioral contract carried over from the original engine:
#   notice() = an EXPECTED benign outcome (AI declined, no changes) → job stays green.
#   fail()   = an UNEXPECTED infra failure (push failed) → non-zero exit, never a silent green.

import os
import sys

# Token usage accumulates here across call_llm() calls and is flushed once at exit.
_USAGE = {"calls": 0, "prompt": 0, "completion": 0, "total": 0}
_USAGE_REPORTED = False


def write_output(name: str, value: str):
    """Write a multi-line value to GITHUB_OUTPUT (heredoc form) and echo to stdout."""
    print(value)
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"{name}<<EOF\n{value}\nEOF\n")


def notice(msg: str):
    """An expected, benign no-op or status update — the job stays green."""
    print(f"::notice::{msg}")


def fail(msg: str, code: int = 1):
    """An unexpected failure — surface it (non-zero exit) instead of a silent green run."""
    print(f"::error::{msg}")
    report_usage()
    sys.exit(code)


def record_usage(usage):
    """Accumulate token usage from one LLM response's `usage` object (tolerant of None)."""
    if not usage:
        return
    _USAGE["calls"] += 1
    _USAGE["prompt"] += getattr(usage, "prompt_tokens", 0) or 0
    _USAGE["completion"] += getattr(usage, "completion_tokens", 0) or 0
    _USAGE["total"] += getattr(usage, "total_tokens", 0) or 0


def report_usage():
    """Print an LLM token-usage summary and append it to the GitHub job summary. Idempotent."""
    global _USAGE_REPORTED
    if _USAGE_REPORTED or _USAGE["calls"] == 0:
        return
    _USAGE_REPORTED = True
    line = (
        f"LLM usage: {_USAGE['calls']} call(s), "
        f"{_USAGE['prompt']} prompt + {_USAGE['completion']} completion "
        f"= {_USAGE['total']} tokens "
        f"(action={os.getenv('AI_ACTION', 'review')}, "
        f"provider={os.getenv('LLM_PROVIDER', 'openai')}, "
        f"model={os.getenv('LLM_MODEL', '')})"
    )
    notice(line)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write(f"### 🤖 LLM usage\n\n{line}\n")
        except OSError:
            pass
