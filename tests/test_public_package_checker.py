"""Spec tests for scripts/check_public_package.py privacy rules.

These three criteria are frozen after the first red-light run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_public_package.py"


def _run_checker(cwd: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    # Always pin --root to the temp tree so the real repo is not scanned.
    args = [sys.executable, str(CHECKER), "--root", str(cwd), *extra]
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
    )


def _init_mini_repo(tmp: Path) -> None:
    """Create a minimal git-tracked tree the checker can scan."""
    (tmp / "src" / "paper_fetch").mkdir(parents=True)
    (tmp / "tests").mkdir(parents=True)
    (tmp / "references").mkdir(parents=True)
    (tmp / "scripts").mkdir(parents=True)
    (tmp / ".github" / "workflows").mkdir(parents=True)

    (tmp / "src" / "paper_fetch" / "__init__.py").write_text(
        '__version__ = "0.3.0"\n', encoding="utf-8"
    )
    (tmp / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "paper-fetch"
            version = "0.3.0"

            [project.scripts]
            paper-fetch = "paper_fetch.cli:main"
            """
        ),
        encoding="utf-8",
    )
    (tmp / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: paper-fetch
            version: 0.3.0
            ---

            See `references/institution-access.md`
            See `references/zotero-upload-protocol.md`
            See `references/ablesci-api-protocol.md`
            See `references/pubmed-linkout.md`
            See `references/zotero-local-write-feasibility.md`
            """
        ),
        encoding="utf-8",
    )
    (tmp / "README.md").write_text("# paper-fetch\n", encoding="utf-8")
    (tmp / ".gitignore").write_text("PROGRESS.md\nBLOCKED.md\n", encoding="utf-8")
    for name in (
        "institution-access.md",
        "zotero-upload-protocol.md",
        "ablesci-api-protocol.md",
        "pubmed-linkout.md",
        "zotero-local-write-feasibility.md",
    ):
        (tmp / "references" / name).write_text(f"# {name}\n", encoding="utf-8")

    # Point checker ROOT via copying script into tmp/scripts and running from tmp
    checker_src = (ROOT / "scripts" / "check_public_package.py").read_text(encoding="utf-8")
    (tmp / "scripts" / "check_public_package.py").write_text(checker_src, encoding="utf-8")

    subprocess.run(["git", "init", "-b", "main"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp,
        check=True,
        capture_output=True,
    )


class TestPrivateConfigScan:
    def test_private_config_value_in_tracked_tree_fails_without_leaking_value(
        self, tmp_path: Path
    ):
        # Build marker without a contiguous credential-assignment literal in this file.
        marker = "-".join(["PROXY", "VALUE", "should", "never", "print", "9f3a"])
        _init_mini_repo(tmp_path)
        # Inject marker into a tracked file
        readme = tmp_path / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") + f"\nproxy={marker}\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "inject"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

        cfg = tmp_path / "private.json"
        cfg.write_text(json.dumps({"clash_proxy": marker}), encoding="utf-8")

        proc = _run_checker(tmp_path, "--private-config", str(cfg))
        assert proc.returncode != 0, proc.stdout + proc.stderr
        combined = proc.stdout + proc.stderr
        assert marker not in combined
        assert "private_config_value" in combined or "clash_proxy" in combined


class TestNumericLocalhostClashProxy:
    def test_numeric_localhost_clash_proxy_fails_default_check(self, tmp_path: Path):
        _init_mini_repo(tmp_path)
        readme = tmp_path / "README.md"
        # Build injection dynamically so this test file itself is not a hit.
        host = "127.0.0." + "1"
        port = str(17000 + 997)
        injected = '\n"clash_proxy": "http://' + host + ":" + port + '"\n'
        readme.write_text(
            readme.read_text(encoding="utf-8") + injected,
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "inject-port"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        proc = _run_checker(tmp_path)
        assert proc.returncode != 0, proc.stdout + proc.stderr
        combined = proc.stdout + proc.stderr
        assert "numeric_localhost_proxy" in combined


class TestAllowedPlaceholders:
    def test_port_placeholder_proxy_example_and_zotero_local_pass(self, tmp_path: Path):
        _init_mini_repo(tmp_path)
        readme = tmp_path / "README.md"
        readme.write_text(
            textwrap.dedent(
                """\
                # paper-fetch
                "clash_proxy": "http://127.0.0.1:<PORT>"
                "clash_proxy": "http://proxy.example:7890"
                zotero://select/library/items/ABCD1234
                """
            ),
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "placeholders"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        proc = _run_checker(tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "PUBLIC_CHECK_OK" in proc.stdout
