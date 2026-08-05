"""CLI tests."""

import json
from pathlib import Path
from unittest.mock import patch

from paper_fetch import __version__
from paper_fetch.cli import main


def test_package_imports():
    assert __version__ == "0.5.0"


def test_public_package_and_cli_names_are_paper_fetch():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text()
    assert 'name = "paper-fetch"' in pyproject
    assert 'paper-fetch = "paper_fetch.cli:main"' in pyproject
    assert "scansci" not in pyproject


def test_help():
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0


def test_help_uses_public_cli_name(capsys):
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "usage: paper-fetch" in capsys.readouterr().out


def test_fetch_help():
    try:
        main(["fetch", "--help"])
    except SystemExit as exc:
        assert exc.code == 0


def test_json_output_on_empty():
    """Empty input causes argparse to fail before reaching the identifier."""
    try:
        main(["fetch", "--json", ""])
    except SystemExit as exc:
        assert exc.code != 0  # Either 2 (argparse) or 3 (resolution)


def test_skill_md_exists():
    """Verify SKILL.md exists in the project root."""
    root = Path(__file__).resolve().parents[1]  # tests/ -> repo root/
    skill = root / "SKILL.md"
    assert skill.exists(), f"SKILL.md not found at {skill}"


def test_skill_md_has_frontmatter():
    """Verify SKILL.md has valid YAML frontmatter."""
    root = Path(__file__).resolve().parents[1]
    skill = root / "SKILL.md"
    content = skill.read_text()
    assert content.startswith("---"), "SKILL.md must start with YAML frontmatter"


def test_skill_md_no_forbidden_terms():
    """SKILL.md must not reference removed features."""
    root = Path(__file__).resolve().parents[1]
    skill = root / "SKILL.md"
    content = skill.read_text().lower()
    forbidden = ["mcp server", "fastapi", "uvicorn", " batch_download", " carsi ", " webvpn "]
    for term in forbidden:
        assert term not in content, f"SKILL.md contains forbidden term: {term}"
