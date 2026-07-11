# AI CI/CD — operational pitfalls

Hard-won traps from building and running this pipeline. When a first run fails, check
here first — most failures are one of these.

| Category | Trap | Resolution |
|---|---|---|
| Branch | `issue_comment` / `workflow_run` / scheduled workflows only use the **default branch's** workflow files | These changes must be merged to the default branch (`main`) to take effect |
| Cross-repo | A **public** repo cannot `uses:` a **private** action (GitHub blocks it to avoid leaking private code) | Make the action repo public, or use `--mode=vendor` |
| Auth | `gh auth login --with-token` conflicts with an already-set `GH_TOKEN` env | Don't add an auth step; `gh` reads `GH_TOKEN` natively |
| Auth | `gh pr list/edit` use GraphQL needing `read:org` scope | With only `repo` scope, use the REST API (`gh api .../pulls`) |
| Push | A `github.token` push does **not** trigger downstream CI (anti-recursion) | Closed-loop auto-fix needs a `GH_PAT` push to re-trigger CI |
| Trigger | A bot comment containing the trigger token (`@coder`) causes a self-trigger loop | Bot output must **never** contain the trigger token; exclude bot comments in the job `if` |
| Concurrency | `concurrency` is evaluated **before** the job `if` | A skipped self-trigger run can cancel a legitimate in-progress run → use `cancel-in-progress: false` |
| Security | `${{ }}` interpolated into `run:` is an injection point | Always pass via `env:` and reference `"$VAR"`; pass untrusted values to `gh` as argv (`--body-file`) |
| Data | `npm audit --json ... 2>&1` mixes warnings into the JSON and breaks parsing | Use `2>/dev/null` |
| Blind spot | Unit tests don't cover large files → CI shows false green while generated code is broken | AI-generated code must pass an independent gate (syntax / imports / dropped-exports / placeholder) before it's trusted |
| Agent (future) | An autonomous coding agent holding push creds + network is a large attack surface | Privilege separation (agent doesn't push) + the verification gate as the sole trust boundary + sandbox/allowlist |

## Distinguishing "no-op" from "failure"

The engine deliberately separates these so a green check means something:
- AI declined / no changes / tests still failing → `::notice::` + exit 0 (expected, stays green).
- Infrastructure failure (push failed, missing required input) → `::error::` + non-zero exit.
