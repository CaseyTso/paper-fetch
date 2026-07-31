#!/usr/bin/env python3
"""Deterministic public-package checks for paper-fetch.

Exit 0 only when the tree is safe to publish as CaseyTso/paper-fetch.
Reports path, category, and line number only — never secret values.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CONTROLLED_GLOBS = (
    "LICENSE",
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
    ".claude-plugin/**",
)

_LEGACY_DIR = "scansci" + "-lite"
SKIP_DIR_PARTS = {
    ".venv",
    ".git",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    _LEGACY_DIR,
    _LEGACY_DIR + "-venv-stale-after-move",
    "node_modules",
}

SKIP_NAMES = {
    "PROGRESS.md",
    "BLOCKED.md",
    "uv.lock",
    ".DS_Store",
}

_LEGACY_BASE = "scansci" + "-lite"
LEGACY_NEEDLES = (
    _LEGACY_BASE,
    _LEGACY_BASE.replace("-", "_"),
    "SCANSCI" + "_LITE",
    "." + _LEGACY_BASE,
    "CaseyTso/" + _LEGACY_BASE,
)

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("personal_home_path", re.compile(r"/Users/(?!you\b)[A-Za-z0-9._-]+")),
    ("personal_email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    (
        "credential_assignment",
        re.compile(
            r"(?i)(api[_-]?key|password|token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}"
        ),
    ),
    ("zotero_key_like", re.compile(r"\b[A-Z0-9]{8}\b")),
]

# Concrete digit ports on localhost/loopback. Only flagged in clash_proxy context
# (see scan_file_content). Zotero local MCP and institution socks examples pass.
NUMERIC_LOCALHOST_PORT = re.compile(
    r"(?i)(?:127\.0\.0\.1|localhost):\d{2,5}"
)
CLASH_PROXY_CONTEXT = re.compile(r"(?i)clash[_-]?proxy")

EMAIL_ALLOW = {
    "you@example.com",
    "test@example.com",
    "me@example.com",
}

ZOTERO_CONTEXT = re.compile(
    r"(?i)(zotero|library_id|inbox_collection|api_key|collection_key)"
)

REQUIRED_SKILL_REFS = (
    "references/institution-access.md",
    "references/zotero-upload-protocol.md",
    "references/ablesci-api-protocol.md",
    "references/pubmed-linkout.md",
    "references/zotero-local-write-feasibility.md",
    "references/ablesci-login.md",
    "references/scihub-clash-setup.md",
)

# Non-secret public constants that may appear in both config and docs/code.
PUBLIC_CONSTANTS = {
    "user",
    "group",
    "true",
    "false",
    "https://www.ablesci.com",
    "https://ablesci.com",
}


def _untracked_controlled_files(root: Path) -> list[Path]:
    """Untracked, non-ignored files that match the controlled globs.

    ``git ls-files --others --exclude-standard`` respects .gitignore and the
    repo's exclude file. Only files matching a controlled glob are returned,
    so stray artifacts (JSON configs, archives, editor files) are not scanned.
    """
    try:
        out = subprocess.check_output(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--others",
                "--exclude-standard",
            ],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []
    controlled = {
        p.resolve()
        for pattern in CONTROLLED_GLOBS
        for p in root.glob(pattern)
        if p.is_file()
    }
    result: list[Path] = []
    for raw in out.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="replace")
        p = root / rel
        if p.is_file() and p.resolve() in controlled:
            result.append(p)
    return result


def tracked_files(root: Path) -> list[Path]:
    """Prefer git-tracked files; fall back to controlled globs without .git.

    In a git repository the scanned set is the union of tracked files and
    untracked, non-ignored files matching a controlled glob — a new file
    sitting in the working tree must not hide secrets until it is committed.
    """
    git_dir = root / ".git"
    if git_dir.exists():
        try:
            out = subprocess.check_output(
                ["git", "-C", str(root), "ls-files", "-z"],
                stderr=subprocess.DEVNULL,
            )
            files: list[Path] = []
            for raw in out.split(b"\0"):
                if not raw:
                    continue
                rel = raw.decode("utf-8", errors="replace")
                p = root / rel
                if p.is_file():
                    files.append(p)
            files.extend(_untracked_controlled_files(root))
            return sorted(set(files))
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            pass
    return controlled_files(root)


def controlled_files(root: Path | None = None) -> list[Path]:
    base = root or ROOT
    files: list[Path] = []
    for pattern in CONTROLLED_GLOBS:
        files.extend(base.glob(pattern))
    out: list[Path] = []
    seen: set[Path] = set()
    for p in files:
        if not p.is_file():
            continue
        if p.name in SKIP_NAMES:
            continue
        rel_parts = p.relative_to(base).parts if p.is_relative_to(base) else p.parts
        if any(part in SKIP_DIR_PARTS for part in rel_parts):
            continue
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return sorted(out)


def rel(path: Path, root: Path | None = None) -> str:
    base = root or ROOT
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def load_private_config(path: Path) -> dict[str, str]:
    """Return field->string value for non-empty non-public config strings."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"PRIVATE_CONFIG_FAIL path_missing:{path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"PRIVATE_CONFIG_FAIL json_invalid:{path}") from exc
    if not isinstance(raw, dict):
        raise SystemExit(f"PRIVATE_CONFIG_FAIL json_invalid:{path}")

    values: dict[str, str] = {}
    for key, val in raw.items():
        if isinstance(val, bool) or val is None:
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            # numeric non-bool — skip as non-string secret material for text scan
            continue
        if not isinstance(val, str):
            continue
        text = val.strip()
        if not text:
            continue
        if text.lower() in {c.lower() for c in PUBLIC_CONSTANTS}:
            continue
        # Ignore very short generic tokens
        if len(text) < 5:
            continue
        values[str(key)] = text
    return values


def scan_file_content(
    path: Path,
    text: str,
    errors: list[str],
    root: Path,
    private_values: dict[str, str] | None = None,
) -> None:
    for i, line in enumerate(text.splitlines(), start=1):
        low = line.lower()
        for needle in LEGACY_NEEDLES:
            if needle.isupper():
                if needle in line:
                    errors.append(f"{rel(path, root)}:{i}:legacy_name:{needle}")
            else:
                if needle.lower() in low:
                    errors.append(f"{rel(path, root)}:{i}:legacy_name:{needle}")

        for _ in SECRET_PATTERNS[0][1].finditer(line):
            errors.append(f"{rel(path, root)}:{i}:personal_home_path")

        for m in SECRET_PATTERNS[1][1].finditer(line):
            email = m.group(0)
            if email.lower() in {e.lower() for e in EMAIL_ALLOW}:
                continue
            if email.lower().endswith("@example.com") or email.lower().endswith(
                "@example.org"
            ):
                continue
            errors.append(f"{rel(path, root)}:{i}:personal_email")

        if SECRET_PATTERNS[2][1].search(line):
            if re.search(r"(?i)(YOUR_|REDACTED|example|xxx|changeme|<|\.\.\.)", line):
                pass
            else:
                errors.append(f"{rel(path, root)}:{i}:credential_assignment")

        if ZOTERO_CONTEXT.search(line):
            for m in SECRET_PATTERNS[3][1].finditer(line):
                token = m.group(0)
                if token in {"YOUR_KEY", "ABCD1234"} or token.startswith("YOUR"):
                    continue
                if re.fullmatch(r"[A-Z0-9]{8}", token) and not re.search(
                    r"(?i)(YOUR_|EXAMPLE|REDACT|PLACEHOLDER|XXXX)", line
                ):
                    if re.search(r"(?i)(api_key|library_id|collection_key)\s*[:=]", line):
                        if not re.search(r"(?i)(YOUR_|REDACT|EXAMPLE|xxx|12345)", line):
                            errors.append(f"{rel(path, root)}:{i}:credential_id_like")

        # clash_proxy with a concrete localhost digit port (not <PORT>).
        if CLASH_PROXY_CONTEXT.search(line) and NUMERIC_LOCALHOST_PORT.search(line):
            if "<PORT>" not in line:
                errors.append(f"{rel(path, root)}:{i}:numeric_localhost_proxy")

        if private_values:
            for field, value in private_values.items():
                if value and value in line:
                    errors.append(
                        f"{rel(path, root)}:{i}:private_config_value:field={field}"
                    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Public package safety checks")
    parser.add_argument(
        "--private-config",
        type=Path,
        default=None,
        help="JSON config whose non-public string values must not appear in tracked files",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: parent of scripts/)",
    )
    args = parser.parse_args(argv)

    root = (args.root or ROOT).resolve()
    errors: list[str] = []

    private_values: dict[str, str] | None = None
    if args.private_config is not None:
        cfg_path = args.private_config.expanduser().resolve()
        if not cfg_path.is_file():
            print(f"PRIVATE_CONFIG_FAIL path_missing:{cfg_path}")
            return 1
        try:
            private_values = load_private_config(cfg_path)
        except SystemExit as exc:
            print(str(exc))
            return 1

    init_path = root / "src/paper_fetch/__init__.py"
    if not init_path.is_file():
        errors.append("src/paper_fetch/__init__.py:missing")
    else:
        init_text = init_path.read_text(encoding="utf-8")
        m = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
        if not m or m.group(1) != "0.3.0":
            errors.append(
                f"src/paper_fetch/__init__.py:version: expected 0.3.0, got {m.group(1) if m else None}"
            )

    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        errors.append("pyproject.toml:missing")
    else:
        pyproject = pyproject_path.read_text(encoding="utf-8")
        if not re.search(r'^version\s*=\s*"0\.3\.0"\s*$', pyproject, re.M):
            errors.append("pyproject.toml:version: expected 0.3.0")
        if 'name = "paper-fetch"' not in pyproject:
            errors.append("pyproject.toml:name: expected paper-fetch")
        if 'paper-fetch = "paper_fetch.cli:main"' not in pyproject:
            errors.append("pyproject.toml:scripts: missing paper-fetch CLI entry")
        if 'license = "AGPL-3.0-only"' not in pyproject:
            errors.append("pyproject.toml:license: expected AGPL-3.0-only")
        if 'license-files = ["LICENSE"]' not in pyproject:
            errors.append("pyproject.toml:license-files: expected LICENSE")

    license_path = root / "LICENSE"
    if not license_path.is_file():
        errors.append("LICENSE:missing")
    else:
        license_text = license_path.read_text(encoding="utf-8")
        if "GNU AFFERO GENERAL PUBLIC LICENSE" not in license_text:
            errors.append("LICENSE:content: expected GNU AGPL text")
        if "Version 3, 19 November 2007" not in license_text:
            errors.append("LICENSE:version: expected AGPL version 3")

    readme_path = root / "README.md"
    if not readme_path.is_file():
        errors.append("README.md:missing")
    else:
        readme = readme_path.read_text(encoding="utf-8")
        if "AGPL-3.0-only" not in readme or "](LICENSE)" not in readme:
            errors.append("README.md:license: expected AGPL-3.0-only link")

    skill_path = root / "SKILL.md"
    if not skill_path.is_file():
        errors.append("SKILL.md:missing")
        skill = ""
    else:
        skill = skill_path.read_text(encoding="utf-8")
        if not re.search(r"^name:\s*paper-fetch\s*$", skill, re.M):
            errors.append("SKILL.md:name: expected paper-fetch")
        if not re.search(r"^version:\s*0\.3\.0\s*$", skill, re.M):
            errors.append("SKILL.md:version: expected 0.3.0")
        if not re.search(r"^license:\s*AGPL-3\.0-only\s*$", skill, re.M):
            errors.append("SKILL.md:license: expected AGPL-3.0-only")

    for ref in REQUIRED_SKILL_REFS:
        if not (root / ref).is_file():
            errors.append(f"{ref}:missing: required skill reference file")
        if skill and ref not in skill and Path(ref).name not in skill:
            errors.append(f"SKILL.md:reference_link: missing mention of {ref}")

    files = tracked_files(root)
    # Always include controlled globs union so untracked-but-present public sources
    # are still checked when falling back; with git, stick to tracked only.
    for path in files:
        # Skip binary-ish
        if path.suffix.lower() in {".pyc", ".pdf", ".png", ".jpg", ".zip", ".bundle"}:
            continue
        if path.name in SKIP_NAMES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        scan_file_content(path, text, errors, root, private_values)

    gitignore_path = root / ".gitignore"
    gitignore = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    for name in ("PROGRESS.md", "BLOCKED.md"):
        if name not in gitignore:
            errors.append(f".gitignore:missing_ignore:{name}")

    if errors:
        # Deduplicate while preserving order
        seen: set[str] = set()
        uniq: list[str] = []
        for e in errors:
            if e not in seen:
                seen.add(e)
                uniq.append(e)
        print(f"PUBLIC_CHECK_FAIL count={len(uniq)}")
        for e in uniq:
            print(e)
        return 1

    print("PUBLIC_CHECK_OK")
    print("version=0.3.0")
    print(f"tracked_files={len(files)}")
    print("legacy_names=0")
    print("cli_entry=paper-fetch")
    print("license=AGPL-3.0-only")
    print(f"skill_refs={len(REQUIRED_SKILL_REFS)}")
    if private_values is not None:
        print(f"private_fields_scanned={len(private_values)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
