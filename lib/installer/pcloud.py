from __future__ import annotations

import json
import os
import subprocess

from .common import say, warn, ok, TTY


def _prompt(text: str) -> str:
    if TTY is None:
        return ""
    print(text, end="", flush=True)
    return TTY.readline().strip()

RCLONE_REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")


def _sanitize_oneline(text: str) -> str:
    return text.replace("\r", "").replace("\n", "").replace("\0", "")


def _timeout(seconds: int, cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, timeout=seconds, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _pcloud_remote_exists() -> bool:
    try:
        res = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True, check=True)
        return any(line.strip() == f"{RCLONE_REMOTE_NAME}:" for line in res.stdout.splitlines())
    except Exception:
        return False


def _pcloud_remote_ok() -> bool:
    if not _pcloud_remote_exists():
        return False
    return _timeout(10, ["rclone", "about", f"{RCLONE_REMOTE_NAME}:"])


def _pcloud_create_oauth_remote(token_json: str, host: str) -> None:
    subprocess.run(["rclone", "config", "delete", RCLONE_REMOTE_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        [
            "rclone",
            "config",
            "create",
            RCLONE_REMOTE_NAME,
            "pcloud",
            "token",
            token_json,
            "hostname",
            host,
            "--non-interactive",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _pcloud_set_oauth_token_autoregion(token_json: str) -> bool:
    for host in ["api.pcloud.com", "eapi.pcloud.com"]:
        _pcloud_create_oauth_remote(token_json, host)
        if _pcloud_remote_ok():
            ok(f"pCloud remote '{RCLONE_REMOTE_NAME}:' configured for {host}.")
            return True
    return False


def _pcloud_webdav_create(email: str, password: str, host: str) -> None:
    obscured = subprocess.check_output(["rclone", "obscure", password], text=True).strip()
    subprocess.run(["rclone", "config", "delete", RCLONE_REMOTE_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        [
            "rclone",
            "config",
            "create",
            RCLONE_REMOTE_NAME,
            "webdav",
            "--non-interactive",
            "--",
            "vendor",
            "other",
            "url",
            host,
            "user",
            email,
            "pass",
            obscured,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _pcloud_webdav_try_both(email: str, password: str) -> bool:
    say("Trying EU WebDAV endpoint…")
    _pcloud_webdav_create(email, password, "https://ewebdav.pcloud.com")
    if _timeout(8, ["rclone", "lsd", f"{RCLONE_REMOTE_NAME}:"]):
        ok("Connected via EU WebDAV.")
        return True
    warn("EU endpoint failed. Trying Global endpoint…")
    _pcloud_webdav_create(email, password, "https://webdav.pcloud.com")
    if _timeout(8, ["rclone", "lsd", f"{RCLONE_REMOTE_NAME}:"]):
        ok("Connected via Global WebDAV.")
        return True
    return False


def ensure_pcloud_remote_or_menu() -> None:
    if _pcloud_remote_ok():
        ok(f"pCloud remote '{RCLONE_REMOTE_NAME}:' is ready.")
        return

    if TTY is None:
        warn("No interactive TTY; skipping pCloud configuration for now.")
        return

    while True:
        print()
        say("Choose how to connect to pCloud:")
        print("  1) Paste OAuth token JSON (recommended)")
        print("  2) Headless OAuth helper")
        print("  3) Try legacy WebDAV")
        print("  4) Skip")
        choice = _prompt("Choose [1-4] [1]: ") or "1"

        if choice in {"1", "2"}:
            say('On any machine with a browser, run:  rclone authorize "pcloud"')
            token = _sanitize_oneline(_prompt("Paste token JSON here: "))
            if not token:
                warn("Empty token.")
                continue
            try:
                json.loads(token)
            except Exception:
                warn("Token does not look like JSON with access_token.")
                continue
            if _pcloud_set_oauth_token_autoregion(token):
                return
            warn("Token invalid or not valid for either region. Try again.")

        elif choice == "3":
            email = _prompt("pCloud login email: ").strip()
            if not email:
                warn("Email required.")
                continue
            import getpass

            password = getpass.getpass("pCloud password (or App Password): ", stream=TTY)
            if not password:
                warn("Password required.")
                continue
            if _pcloud_webdav_try_both(email, password) and _pcloud_remote_ok():
                ok("pCloud remote configured.")
                return
            warn("Authentication failed on both endpoints.")

        elif choice == "4":
            warn("Skipping pCloud configuration for now.")
            return

        else:
            warn("Invalid choice.")
