"""Contract tests: new config/env names + zero legacy names in controlled files."""

from __future__ import annotations

import re
from pathlib import Path

from paper_fetch.config import CONFIG_DIR, DEFAULT_CONFIG_PATH, load_config

ROOT = Path(__file__).resolve().parents[1]

# Controlled tree: tracked-style sources that must not carry legacy names.
CONTROLLED_GLOBS = (
    "README.md",
    "SKILL.md",
    "pyproject.toml",
    ".gitignore",
    "src/**/*.py",
    "tests/**/*.py",
    "tests/**/*.html",
    "references/**/*.md",
    "scripts/**/*.py",
    ".github/**/*",
)

# Build legacy needles dynamically so this test file itself is not a hit source.
_LEGACY_BASE = "scansci" + "-lite"
LEGACY_NEEDLES = (
    _LEGACY_BASE,
    _LEGACY_BASE.replace("-", "_"),
    "SCANSCI" + "_LITE",
    "." + _LEGACY_BASE,
    "CaseyTso/" + _LEGACY_BASE,
)

SKIP_SUFFIXES = {".pyc", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".bundle"}


def _controlled_files() -> list[Path]:
    files: list[Path] = []
    for pattern in CONTROLLED_GLOBS:
        files.extend(ROOT.glob(pattern))
    out: list[Path] = []
    seen: set[Path] = set()
    for p in files:
        if not p.is_file():
            continue
        if p.suffix.lower() in SKIP_SUFFIXES:
            continue
        if p.name in {"PROGRESS.md", "BLOCKED.md", "uv.lock"}:
            continue
        if any(
            part in {".venv", ".git", "dist", "build", "__pycache__", ".pytest_cache"}
            for part in p.parts
        ):
            continue
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return sorted(out)


class TestNewConfigPaths:
    def test_config_dir_is_paper_fetch(self):
        assert CONFIG_DIR == Path.home() / ".paper-fetch"
        assert DEFAULT_CONFIG_PATH == CONFIG_DIR / "config.json"

    def test_default_output_dir_uses_paper_fetch(self):
        c = load_config(path="/nonexistent/paper-fetch-missing.json")
        assert "paper-fetch" in str(c.output_dir)
        assert "scansci" not in str(c.output_dir).lower()

    def test_env_prefix_is_paper_fetch(self, monkeypatch):
        monkeypatch.delenv("PAPER_FETCH_ZOTERO_API_KEY", raising=False)
        legacy_env = "SCANSCI" + "_LITE_ZOTERO_API_KEY"
        monkeypatch.delenv(legacy_env, raising=False)
        monkeypatch.setenv("PAPER_FETCH_ZOTERO_API_KEY", "new-env-key")
        # Legacy env must NOT be read
        monkeypatch.setenv(legacy_env, "legacy-should-be-ignored")
        c = load_config(path="/nonexistent/paper-fetch-missing.json")
        assert c.zotero_api_key == "new-env-key"

    def test_env_clash_proxy_paper_fetch(self, monkeypatch):
        monkeypatch.setenv("PAPER_FETCH_CLASH_PROXY", "http://env-proxy:7890")
        c = load_config(path="/nonexistent/paper-fetch-missing.json")
        assert c.clash_proxy == "http://env-proxy:7890"


class TestNoLegacyNamesInControlledTree:
    def test_zero_legacy_names(self):
        hits: list[str] = []
        needles_lower = [n.lower() for n in LEGACY_NEEDLES]
        for path in _controlled_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                low = line.lower()
                for needle, needle_l in zip(LEGACY_NEEDLES, needles_lower):
                    # Case-sensitive for ALLCAPS token; case-insensitive otherwise
                    if needle.isupper():
                        if needle in line:
                            rel = path.relative_to(ROOT)
                            hits.append(f"{rel}:{i}:{needle}")
                    else:
                        if needle_l in low:
                            rel = path.relative_to(ROOT)
                            hits.append(f"{rel}:{i}:{needle}")
        assert hits == [], "legacy names still present:\n" + "\n".join(hits)


class TestVersionsAligned:
    def test_package_version_is_030(self):
        from paper_fetch import __version__

        assert __version__ == "0.3.0"

    def test_pyproject_version_is_030(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert re.search(r'^version\s*=\s*"0\.3\.0"\s*$', text, re.M)

    def test_skill_version_is_030(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        assert re.search(r"^version:\s*0\.3\.0\s*$", text, re.M)
        assert re.search(r"^name:\s*paper-fetch\s*$", text, re.M)
