from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TEXT = (
    "395" + "32023",
    "516" + "10839",
    "yo" + "sino",
    "can" + "on64",
)
FORBIDDEN_PATH = re.compile(r"(?i)(?<![A-Z0-9])[A-Z]:\\(?:Users|tools|utility|workspaces|project_root|kks)\\")
PRIVATE_NETWORK = re.compile(
    r"(?<![0-9])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3})(?![0-9])"
)
SECRET_LITERAL = re.compile(
    r"(?i)(?:api[_-]?key|password|secret|token|cookie|user_session)\s*[:=]\s*['\"][^'\"]{8,}['\"]"
)
SENSITIVE_NAMES = re.compile(
    r"(?i)(?:^|/)(?:config(?:\.local|\.private)?\.json|\.env(?:\..+)?|"
    r"credentials.*\.json|secrets.*\.json|tokens.*\.json|cookies.*\.json|"
    r"sessions.*\.json|auth_state.*\.json|Login Data|Local State|History)$"
)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / name.decode("utf-8") for name in result.stdout.split(b"\0") if name]


def main() -> int:
    problems: list[str] = []
    for path in tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        if SENSITIVE_NAMES.search(relative):
            problems.append(f"sensitive filename: {relative}")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            problems.append(f"unreadable tracked file: {relative}: {exc}")
            continue
        if b"\0" in payload:
            lowered_payload = payload.lower()
            for value in FORBIDDEN_TEXT:
                if value.encode("utf-8").lower() in lowered_payload:
                    problems.append(f"forbidden identity in binary: {relative}")
            if ("C:" + "\\Users\\" + "youzo").encode("utf-8").lower() in lowered_payload:
                problems.append(f"personal machine path in binary: {relative}")
            continue
        text = payload.decode("utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            lowered = line.casefold()
            for value in FORBIDDEN_TEXT:
                if value.casefold() in lowered:
                    problems.append(f"forbidden identity: {relative}:{line_no}")
            if FORBIDDEN_PATH.search(line):
                problems.append(f"machine-specific path: {relative}:{line_no}")
            if PRIVATE_NETWORK.search(line):
                problems.append(f"private network address: {relative}:{line_no}")
            if SECRET_LITERAL.search(line):
                problems.append(f"possible embedded secret: {relative}:{line_no}")
    if problems:
        print("Public privacy check failed:", file=sys.stderr)
        for problem in sorted(set(problems)):
            print(f"- {problem}", file=sys.stderr)
        return 1
    print(f"Public privacy check passed: {len(tracked_files())} tracked files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
