#!/usr/bin/env python3
"""One-time bootstrap: run Google OAuth consent flow, persist the refresh token.

Run once on Mac after creating the OAuth client in Google Cloud Console (Phase 4.4
setup). Writes `google_token.json` to GOOGLE_OAUTH_TOKEN_PATH. Subsequent integration
calls reuse the persisted token, auto-refreshing access tokens via the refresh_token
without browser involvement.

In Phase 9, scp the resulting token file to the VPS — refresh tokens bind to the
OAuth client_id, not the machine.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".claude" / ".env")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events.readonly",
]

DEFAULT_TOKEN_PATH = ".claude/data/state/google_token.json"


def _resolve(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs)
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


def main() -> int:
    secrets_path = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRETS_PATH")
    token_path = os.environ.get("GOOGLE_OAUTH_TOKEN_PATH", DEFAULT_TOKEN_PATH)
    if not secrets_path:
        print("ERROR: GOOGLE_OAUTH_CLIENT_SECRETS_PATH not set in .env", file=sys.stderr)
        return 1
    secrets_abs = _resolve(secrets_path)
    token_abs = _resolve(token_path)
    if not secrets_abs.exists():
        print(f"ERROR: client_secrets file not found at {secrets_abs}", file=sys.stderr)
        return 1
    if token_abs.exists():
        print(f"Token already exists at {token_abs}")
        print("Re-running will overwrite (forces re-consent). Continue? [y/N] ",
              end="", flush=True)
        ans = sys.stdin.readline().strip().lower()
        if ans != "y":
            print("Aborted.")
            return 0

    print(f"Running OAuth consent flow with client_secrets: {secrets_abs}")
    print()
    print("A browser window will open. Sign in as brunofbouwman@gmail.com.")
    print('You will see an "unverified app" warning — click Advanced ->')
    print("Go to BrunOS (unsafe). Google permits this for the OAuth project")
    print("owner's own Google account.")
    print()
    print("Approve the requested scopes:")
    for s in SCOPES:
        print(f"  - {s}")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_abs), SCOPES)
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    token_abs.parent.mkdir(parents=True, exist_ok=True)
    token_abs.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(token_abs, 0o600)
    except OSError:
        pass

    print()
    print(f"Token saved to: {token_abs}")
    print(f"  has refresh_token: {bool(creds.refresh_token)}")
    print(f"  scopes granted: {sorted(creds.scopes or [])}")
    print()
    if not creds.refresh_token:
        print("WARNING: no refresh_token returned. The token will work for ~1h then break.")
        print("Re-run this script with `prompt='consent'` to force one (already set).")
        print("If the issue persists, revoke prior consent at:")
        print("  https://myaccount.google.com/permissions")
        print("then re-run.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
