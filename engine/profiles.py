# profiles.py — language verification profiles.
#
# The deep code-generation gates (symbol scanning, import-hallucination checks,
# dropped-export detection) are inherently language-specific. Each profile
# implements what it CAN for its language and degrades gracefully; the generic
# fallback keeps the language-agnostic gates (placeholders, configured syntax
# command, test suite) so every language gets a real, if shallower, gate.

import os
import re
import shlex
import subprocess

_PLACEHOLDER_PHRASES = (
    "rest of the", "rest of existing", "rest of your", "existing code continues",
    "existing code remains", "code continues below", "for brevity", "keep existing code",
    "remainder of the file", "same as before", "remains unchanged", "logic remains",
)


class Profile:
    """Base profile: language-agnostic gates. Subclasses add language-specific ones."""

    name = "generic"

    def __init__(self, cfg):
        self.cfg = cfg

    def is_source_file(self, path: str) -> bool:
        return any(path.endswith(ext) for ext in self.cfg.file_exts)

    # — language-agnostic —

    def find_placeholders(self, code: str) -> list:
        """Comment lines that look like 'skip the rest of the file' placeholders —
        the classic way an LLM silently truncates a file it was told to reproduce."""
        bad = []
        for raw in code.splitlines():
            s = raw.strip()
            if not (s.startswith("//") or s.startswith("*") or s.startswith("/*") or s.startswith("#")):
                continue
            low = s.lower()
            if any(p in low for p in _PLACEHOLDER_PHRASES):
                bad.append(s[:120])
            elif ("..." in low or "…" in low) and any(
                k in low for k in ("rest", "existing", "remaining", "omitted", "unchanged")
            ):
                bad.append(s[:120])
        return bad

    def _default_syntax_check(self) -> str:
        return ""

    def syntax_check(self, filepath: str):
        """Run the configured (or profile-default) syntax check. Returns (ok, message)."""
        tmpl = self.cfg.syntax_check or self._default_syntax_check()
        if not tmpl:
            return True, "(syntax check skipped — no command configured)"
        parts = [p.replace("{file}", filepath) for p in shlex.split(tmpl)]
        try:
            r = subprocess.run(parts, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            return False, f"syntax check could not run: {e}"
        if r.returncode != 0:
            last = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "syntax error"
            return False, last
        return True, ""

    # — language-specific hooks (no-ops in the generic base) —

    def scan_symbols(self, source_dir: str):
        """Return (export_map, api_reference_text). Generic: nothing to scan."""
        return {}, ""

    def validate_imports(self, files, export_map) -> list:
        """Return a list of import errors. Generic: no static import validation."""
        return []


class NodeProfile(Profile):
    """JavaScript/Node profile — ports the original t-cli JS gates."""

    name = "node"

    def _default_syntax_check(self) -> str:
        return "node --check {file}"

    def scan_symbols(self, source_dir="src"):
        """Walk `source_dir` and return (export_map, api_reference_text).

        export_map: {normalized_path: [exported_name, ...]} — for static import validation.
        api_reference_text: export signatures so the model imports real symbols, not guesses."""
        export_map = {}
        blocks = []
        sig_re = re.compile(
            r"\s*export\s+(?:default\s+)?(?:async\s+)?(?:function|const|class)\s+(\w+)(.*)"
        )
        for dirpath, dirs, files in os.walk(source_dir):
            dirs[:] = [d for d in dirs if not d.startswith("_") and d != "node_modules"]
            for fname in sorted(files):
                if not self.is_source_file(fname):
                    continue
                path = os.path.normpath(os.path.join(dirpath, fname))
                names, sigs = [], []
                try:
                    with open(path, encoding="utf-8") as fh:
                        for line in fh:
                            m = sig_re.match(line)
                            if m:
                                names.append(m.group(1))
                                sigs.append(line.strip().rstrip("{").strip())
                except OSError:
                    continue
                export_map[path] = names
                if sigs:
                    blocks.append(f"{path}:\n  " + "\n  ".join(sigs))
        return export_map, "\n".join(blocks)

    def validate_imports(self, files, export_map) -> list:
        """Static check: every local (./ or ../) named import must resolve to a file
        that actually exports the symbol. Catches hallucinated imports without executing."""
        errors = []
        named_re = re.compile(r"import\s+(?:\w+\s*,\s*)?\{([^}]*)\}\s+from\s+['\"](\.[^'\"]+)['\"]")
        path_re = re.compile(r"from\s+['\"](\.[^'\"]+)['\"]")
        for fp in files:
            if not self.is_source_file(fp):
                continue
            try:
                with open(fp, encoding="utf-8") as fh:
                    src = fh.read()
            except OSError:
                continue
            base = os.path.dirname(fp)
            for pm in path_re.finditer(src):
                target = os.path.normpath(os.path.join(base, pm.group(1)))
                if not os.path.exists(target):
                    errors.append(
                        f"{fp}: import path '{pm.group(1)}' resolves to '{target}', which does not exist"
                    )
            for m in named_re.finditer(src):
                names = [n.strip().split(" as ")[0].strip() for n in m.group(1).split(",") if n.strip()]
                target = os.path.normpath(os.path.join(base, m.group(2)))
                avail = export_map.get(target)
                if avail is None:
                    continue  # not a scanned module (or missing path already reported)
                for n in names:
                    if n and n not in avail:
                        errors.append(
                            f"{fp}: imports {{{n}}} from '{m.group(2)}', but that module exports: "
                            f"{', '.join(avail) or '(none)'}"
                        )
        return errors


_PROFILES = {
    "node": NodeProfile,
    "javascript": NodeProfile,
    "js": NodeProfile,
    "generic": Profile,
}


def get_profile(cfg) -> Profile:
    """Pick a language profile from cfg.language, defaulting to the generic base."""
    return _PROFILES.get(cfg.language, Profile)(cfg)
