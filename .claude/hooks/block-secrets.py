#!/usr/bin/env python3
"""PreToolUse hook: block credential, private, and financial file access.

Stdlib only — runs under system python3 (no .venv).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

CREDENTIAL_PATH_PATTERNS = [
    r"(^|/)\.env(\.|$)",
    r"(^|/)\.env$",
    r"\.pem$",
    r"\.key$",
    r"(^|/)id_rsa(\.|$)",
    r"(^|/)id_ed25519(\.|$)",
    r"(^|/)id_ecdsa(\.|$)",
    r"(^|/)credentials\.json$",
    r"(^|/)google_token\.json$",
    r"(^|/)client_secrets?\.json$",
    r"(^|/)\.aws/credentials",
    r"(^|/)\.aws/config",
    r"(^|/)\.ssh/",
    r"(^|/)\.config/gh/",
    r"(^|/)\.netrc$",
    r"/secrets/",
    r"/private/",
    r"(^|/)finance[^/]*",
    r"finance\.md$",
    r"finance/",
    r"invoice",
    r"billing",
    r"payment",
]

ENV_EXFIL_BASH_PATTERNS = [
    r"\bcat\s+(\.env(\.[^\s]+)?|.*/\.env(\.[^\s]+)?|.*\.pem|.*\.key)\b",
    r"\bhead\s+(\.env(\.[^\s]+)?|.*/\.env(\.[^\s]+)?)\b",
    r"\btail\s+(\.env(\.[^\s]+)?|.*/\.env(\.[^\s]+)?)\b",
    r"\bless\s+(\.env(\.[^\s]+)?|.*/\.env(\.[^\s]+)?)\b",
    r"\bprintenv\b",
    r"\benv\s*$",
    r"\benv\s*\|",
    r"\becho\s+\$[A-Z_]+TOKEN\b",
    r"\becho\s+\$[A-Z_]+API_?KEY\b",
    r"\becho\s+\$[A-Z_]+SECRET\b",
    r"\bpython\d?\s+-c\s+.*os\.environ",
    r"\bnode\s+-e\s+.*process\.env",
    r"\bpython\d?\s+-c\s+.*open\(['\"](\.env|/[^'\"]*\.env)",
]

_SUBSHELL = re.compile(r"\$\(([^)]*)\)|`([^`]*)`")
_PATH_PREFIX = re.compile(r"(^|\s)/(usr/local/|usr/|)bin/")


def _normalize_command(cmd: str, depth: int = 0) -> list[str]:
    if depth > 5:
        return [_PATH_PREFIX.sub(r"\1", cmd)]
    out = [_PATH_PREFIX.sub(r"\1", cmd)]
    for match in _SUBSHELL.finditer(cmd):
        inner = match.group(1) or match.group(2) or ""
        out.extend(_normalize_command(inner, depth + 1))
    return out


def _candidate_paths(raw_path: str) -> list[str]:
    if not raw_path:
        return []
    raw = str(raw_path)
    candidates = [raw, raw.replace("\\", "/")]
    expanded = Path(raw).expanduser()
    if expanded.is_absolute():
        candidates.append(str(expanded.resolve(strict=False)))
    else:
        candidates.append(str((REPO_ROOT / expanded).resolve(strict=False)))
    return [c.replace("\\", "/") for c in candidates]


def _path_match(raw_path: str) -> str | None:
    for candidate in _candidate_paths(raw_path):
        for pattern in CREDENTIAL_PATH_PATTERNS:
            if re.search(pattern, candidate, flags=re.IGNORECASE):
                return pattern
    return None


def _bash_match(command: str) -> str | None:
    for variant in _normalize_command(command):
        for pattern in ENV_EXFIL_BASH_PATTERNS:
            if re.search(pattern, variant, flags=re.IGNORECASE):
                return pattern
    return None


def _emit_block(reason: str) -> None:
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    sys.stdout.flush()


def _check_path_tool(tool_name: str, tool_input: dict) -> str | None:
    if tool_name in ("Read", "Edit", "Write", "MultiEdit"):
        return _path_match(str(tool_input.get("file_path") or ""))
    if tool_name == "Glob":
        return _path_match(str(tool_input.get("pattern") or "")) or _path_match(
            str(tool_input.get("path") or "")
        )
    if tool_name == "Grep":
        return _path_match(str(tool_input.get("path") or ""))
    return None


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    if not raw:
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    if tool_name == "Bash":
        matched = _bash_match(str(tool_input.get("command") or ""))
        if matched:
            _emit_block(f"{matched} matches an environment exfiltration pattern")
        return 0

    matched = _check_path_tool(tool_name, tool_input)
    if matched:
        _emit_block(f"{matched} matches a credential/private path pattern")
    return 0


if __name__ == "__main__":
    sys.exit(main())
