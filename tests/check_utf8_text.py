from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURRENT_FILE = Path(__file__).resolve()
INTENTIONAL_TOKEN_FILES = {ROOT / "AGENTS.md"}

TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".txt",
    ".ini",
    ".cfg",
}

SKIP_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    "abasub",
    "build",
    "dist",
}

SUSPICIOUS_TOKENS = (
    "瀹",
    "缁",
    "锛",
    "鍚",
    "�",
    "????",
)


def should_check(path: Path) -> bool:
    resolved_path = path.resolve()

    if resolved_path == CURRENT_FILE or resolved_path in INTENTIONAL_TOKEN_FILES:
        return False

    if path.suffix.lower() not in TEXT_SUFFIXES:
        return False

    return not any(part in SKIP_DIRS for part in path.parts)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    errors: list[str] = []

    for path in ROOT.rglob("*"):
        if not path.is_file() or not should_check(path):
            continue

        relative_path = path.relative_to(ROOT)

        try:
            text = path.read_text(
                encoding="utf-8",
                errors="strict",
            )

        except UnicodeDecodeError as exc:
            errors.append(f"[非 UTF-8 文件] {relative_path}: {exc}")

            continue

        for line_number, line in enumerate(
            text.splitlines(),
            start=1,
        ):
            matches = [token for token in SUSPICIOUS_TOKENS if token in line]

            if matches:
                errors.append(f"[疑似乱码] {relative_path}:{line_number} 命中 {matches!r}\n    {line.strip()}")

    if errors:
        print("\n\n".join(errors))

        return 1

    print("UTF-8 and mojibake checks passed.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
