# config.py — load and validate the per-project `.github/ai-cicd.yml`.
#
# This is the de-t-cli-fication layer: every value that used to be hardcoded in
# ai_agent.py (project description, test command, source dir, file extensions,
# syntax-check command, ...) now comes from here, so the same engine drives any
# project by shipping a different ai-cicd.yml.

import os
from dataclasses import dataclass, field, replace

try:
    import yaml
except ImportError:  # pragma: no cover - surfaced clearly at runtime
    yaml = None

DEFAULT_CONFIG_PATH = ".github/ai-cicd.yml"

# Conventional lock files excluded from diffs so token budget isn't wasted on churn.
_DEFAULT_LOCK_EXCLUDES = (
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "go.sum", "poetry.lock", "Cargo.lock",
)


@dataclass(frozen=True)
class ProjectConfig:
    """Immutable, fully-resolved project configuration for one engine run."""

    # — identity / prompt context —
    name: str = "this project"
    description: str = ""
    conventions: str = ""            # resolved CONTENT of conventions_file (not the path)
    language: str = "generic"

    # — commands —
    test_cmd: str = ""
    build_cmd: str = ""
    syntax_check: str = ""           # template containing '{file}', e.g. 'node --check {file}'
    audit_cmd: str = ""

    # — layout —
    source_dir: str = "src"
    test_dir: str = "test"
    file_exts: tuple = (".js",)
    lock_excludes: tuple = _DEFAULT_LOCK_EXCLUDES

    # — providers / engine —
    default_provider: str = "deepseek"
    default_model: str = "deepseek-chat"
    backend: str = "api"             # api | claude-code | codex | opencode  (P5)
    max_turns: int = 30
    escalate_on_api_failure: bool = True

    # — action switches —
    actions: dict = field(default_factory=dict)

    # — per-action prompt customization —
    # {action_key: {"extra": "...appended...", "system": "...full override..."}}
    prompts: dict = field(default_factory=dict)

    def project_line(self) -> str:
        """One-line project descriptor injected into every system prompt."""
        if self.description:
            return f"{self.name} — {self.description}"
        return self.name

    def action_enabled(self, action: str) -> bool:
        # Default to enabled: absence of an explicit switch means "on".
        return bool(self.actions.get(action, True))

    def system_body(self, action_key: str, default_body: str) -> str:
        """Resolve a system-prompt body for `action_key`, applying project overrides.

        - prompts.<key>.system  → replaces the default body entirely
        - prompts.<key>.extra   → appended to the (possibly overridden) body
        The security INJECTION_GUARD is NOT part of this — callers append it after,
        so a project override can never drop the injection defense. Absent config
        returns default_body unchanged (fully backward compatible)."""
        p = self.prompts.get(action_key) or {}
        body = p.get("system") or default_body
        extra = p.get("extra")
        return f"{body}\n\n{extra}" if extra else body

    def prompt_extra(self, action_key: str) -> str:
        """Project-supplied extra instructions for `action_key` ('' if none).

        For multi-prompt actions (e.g. implement_issue) that append the same extra
        to several internal prompts."""
        p = self.prompts.get(action_key) or {}
        return p.get("extra") or ""


def _read_conventions(conv_file, limit=6000) -> str:
    """Read the project's conventions/architecture doc (CLAUDE.md / AGENTS.md).

    Static gates can't catch framework-model violations (e.g. 'Ink owns stdin');
    this doc is how the codegen prompt learns them. Falls back to the first
    conventional file found when none is configured."""
    candidates = [conv_file] if conv_file else ["CLAUDE.md", ".github/CLAUDE.md", "AGENTS.md"]
    for cand in candidates:
        if cand and os.path.exists(cand):
            try:
                return open(cand, encoding="utf-8").read()[:limit]
            except OSError:
                return ""
    return ""


def load_config(path: str = None) -> ProjectConfig:
    """Load ai-cicd.yml into a ProjectConfig, applying defaults for absent keys.

    A missing file yields an all-defaults config (so the engine degrades to the
    generic profile rather than crashing). Env vars still override provider/model
    downstream, matching the existing workflow wiring."""
    path = path or os.getenv("AI_CICD_CONFIG", DEFAULT_CONFIG_PATH)
    if not os.path.exists(path):
        return ProjectConfig()
    if yaml is None:
        raise RuntimeError("PyYAML is required to read ai-cicd.yml — `pip install pyyaml`")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    proj = raw.get("project", {}) or {}
    cmds = raw.get("commands", {}) or {}
    layout = raw.get("layout", {}) or {}
    providers = raw.get("providers", {}) or {}
    engine = raw.get("engine", {}) or {}
    actions = raw.get("actions", {}) or {}
    # Keep only well-formed {key: {extra?, system?}} entries.
    prompts = {
        k: v for k, v in (raw.get("prompts", {}) or {}).items() if isinstance(v, dict)
    }

    exts = layout.get("file_ext") or layout.get("file_exts")
    file_exts = tuple(exts) if exts else ProjectConfig.file_exts
    lock_excludes = tuple(layout.get("lock_excludes") or _DEFAULT_LOCK_EXCLUDES)

    return ProjectConfig(
        name=proj.get("name") or ProjectConfig.name,
        description=proj.get("description") or "",
        conventions=_read_conventions(proj.get("conventions_file")),
        language=(raw.get("language") or proj.get("language") or "generic").lower(),
        test_cmd=cmds.get("test") or "",
        build_cmd=cmds.get("build") or "",
        syntax_check=cmds.get("syntax_check") or "",
        audit_cmd=cmds.get("audit") or "",
        source_dir=layout.get("source_dir") or "src",
        test_dir=layout.get("test_dir") or "test",
        file_exts=file_exts,
        lock_excludes=lock_excludes,
        default_provider=providers.get("default") or ProjectConfig.default_provider,
        default_model=providers.get("model") or ProjectConfig.default_model,
        backend=(engine.get("backend") or "api").lower(),
        max_turns=int(engine.get("max_turns") or 30),
        escalate_on_api_failure=bool(engine.get("escalate_on_api_failure", True)),
        actions=dict(actions),
        prompts=prompts,
    )
