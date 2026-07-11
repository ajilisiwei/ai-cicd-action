# ai-cicd-action

Reusable, model-agnostic **AI CI/CD** engine — one-command bootstrap of PR Review, Auto-Fix,
Release Notes, Issue Triage, Security Scan, Test Suggestion, and Issue Implementation into any
GitHub project.

Single source of truth for the logic (a composite action) + a bootstrapper Skill that generates
thin per-project workflows. Supports two execution backends:

- **API** — direct cloud-LLM calls (lightweight, deterministic). Default.
- **Coding agent** — Claude Code / Codex / OpenCode running headless in the runner (autonomous,
  multi-file, opt-in) for harder fixes.

> **Status: design baseline (not yet implemented).**
> Extracted from the battle-tested pipeline in the `t-cli` project.

## Design baseline

See **[docs/BASELINE.md](docs/BASELINE.md)** — architecture, parameterization, language profiles,
the coding-agent backend analysis, security model, and the phased roadmap (P0–P7).

## Roadmap (summary)

| Phase | Deliverable |
|---|---|
| P0 | De-`t-cli` the engine → read `ai-cicd.yml`; regress on t-cli |
| P1 | Engine + language profiles (`node`/`generic`) |
| P2 | Composite `action.yml` + `@v1` tag |
| P3 | Bootstrapper Skill (`/init-ai-cicd`) |
| P4 | node profile port + generic fallback (6 actions cross-language) |
| P5 | Coding-agent backends (Claude Code → OpenCode → Codex) |
| P6 | Security hardening (injection isolation, sandbox, budget caps) |
| P7 | End-to-end validation on a real new project + Go/Python deep gates |
