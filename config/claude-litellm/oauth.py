#!/usr/bin/env python3
"""Manage provider OAuth credentials without exposing token material."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import stat
import sys
import tempfile
import time
import types
from pathlib import Path
from typing import Any


PROVIDERS = ("chatgpt", "grok")


def _absolute_env_path(name: str, default: Path | None = None) -> Path:
    value = os.environ.get(name)
    path = Path(value) if value is not None else default
    if path is None:
        raise RuntimeError(f"missing OAuth storage setting: {name}")
    if not path.is_absolute():
        raise RuntimeError(f"OAuth storage path must be absolute: {name}")
    return path


def _auth_filename(name: str, default: str = "auth.json") -> str:
    value = os.environ.get(name, default)
    if (
        not value
        or value in {".", ".."}
        or Path(value).name != value
        or "\x00" in value
    ):
        raise RuntimeError(f"OAuth credential filename must be one path component: {name}")
    return value


def _auth_layout(provider: str) -> tuple[Path, Path, Path, Path, Path]:
    if provider not in PROVIDERS:
        raise RuntimeError(f"unsupported OAuth provider: {provider}")

    package_root = _absolute_env_path("AI_LITELLM_HOME")
    expected_state_root = package_root / "state"
    state_root = _absolute_env_path("AI_LITELLM_STATE_HOME", expected_state_root)
    if state_root != expected_state_root:
        raise RuntimeError("OAuth state root must be AI_LITELLM_HOME/state")
    expected_auth_root = state_root / "auth"
    auth_root = _absolute_env_path("CLAUDE_LITELLM_AUTH_HOME", expected_auth_root)
    if auth_root != expected_auth_root:
        raise RuntimeError("OAuth auth root must be AI_LITELLM_STATE_HOME/auth")

    if provider == "chatgpt":
        expected_provider_root = auth_root / "chatgpt"
        provider_root = _absolute_env_path("CHATGPT_TOKEN_DIR", expected_provider_root)
        filename = _auth_filename("CHATGPT_AUTH_FILE")
    else:
        expected_provider_root = auth_root / "grok"
        provider_root = _absolute_env_path("XAI_OAUTH_TOKEN_DIR", expected_provider_root)
        filename = _auth_filename("XAI_OAUTH_AUTH_FILE")
    if provider_root != expected_provider_root:
        raise RuntimeError(f"{provider} OAuth token directory is outside the managed auth root")
    return package_root, state_root, auth_root, provider_root, provider_root / filename


def _auth_path(provider: str) -> Path:
    return _auth_layout(provider)[4]


def _provider_for_path(path: Path) -> str:
    matches = [provider for provider in PROVIDERS if path == _auth_path(provider)]
    if len(matches) != 1:
        raise RuntimeError(f"OAuth credential path is not a managed provider path: {path}")
    return matches[0]


def _validate_parent_chain(
    provider: str,
    *,
    create: bool,
    repair_permissions: bool,
) -> bool:
    """Validate package/state/auth/provider components without following links.

    The package lifecycle owns and creates the state root. OAuth code may create
    only its two descendants after the state root itself has passed ``lstat``.
    ``False`` means a directory is simply absent in read-only/status mode.
    """
    package_root, state_root, auth_root, provider_root, _path = _auth_layout(provider)
    for index, directory in enumerate((package_root, state_root, auth_root, provider_root)):
        try:
            info = directory.lstat()
        except FileNotFoundError:
            if not create:
                return False
            if index <= 1:
                label = "package root" if index == 0 else "state root"
                raise RuntimeError(f"OAuth {label} does not exist: {directory}") from None
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                # A concurrent creator still has to pass the same lstat checks.
                pass
            info = directory.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RuntimeError(f"unsafe OAuth directory in managed path: {directory}")
        # The package root contains executable/config assets and is normally
        # 0755. Credential-bearing state and its descendants must be private.
        if index == 0:
            continue
        if repair_permissions:
            directory.chmod(0o700)
        elif stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError(f"unsafe OAuth directory permissions: {directory}")
    return True


def _secure_parent(path: Path) -> None:
    provider = _provider_for_path(path)
    _validate_parent_chain(provider, create=True, repair_permissions=True)


def _open_auth_fd(path: Path, *, repair_permissions: bool) -> int | None:
    """Open and validate the credential leaf without an lstat/open race."""
    _provider_for_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f"unsafe OAuth credential path: {path}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"unsafe OAuth credential path: {path}")
        if repair_permissions:
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise RuntimeError(f"unsafe OAuth credential permissions: {path}")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _secure_auth_file(path: Path) -> None:
    fd = _open_auth_fd(path, repair_permissions=True)
    if fd is not None:
        os.close(fd)


def _read_auth_record(path: Path) -> dict[str, Any] | None:
    _secure_parent(path)
    fd = _open_auth_fd(path, repair_permissions=True)
    if fd is None:
        return None
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _atomic_write_auth(path: Path, data: dict[str, Any]) -> None:
    _secure_parent(path)
    _secure_auth_file(path)
    fd, staged_raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    staged = Path(staged_raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _install_safe_chatgpt_file_io(authenticator: Any, path: Path) -> None:
    def read_auth_file(_self: Any) -> dict[str, Any] | None:
        return _read_auth_record(path)

    def write_auth_file(_self: Any, data: dict[str, Any]) -> None:
        _atomic_write_auth(path, data)

    authenticator._read_auth_file = types.MethodType(read_auth_file, authenticator)
    authenticator._write_auth_file = types.MethodType(write_auth_file, authenticator)


def _safe_chatgpt_login(authenticator: Any) -> str:
    """Use LiteLLM's device flow without logging refresh exception payloads."""
    from litellm.llms.chatgpt.common_utils import RefreshAccessTokenError

    auth_data = authenticator._read_auth_file()
    if auth_data:
        access_token = auth_data.get("access_token")
        if access_token and not authenticator._is_token_expired(auth_data, access_token):
            return access_token
        refresh_token = auth_data.get("refresh_token")
        if refresh_token:
            try:
                return authenticator._refresh_tokens(refresh_token)["access_token"]
            except RefreshAccessTokenError:
                # Upstream logs this exception verbatim and its message can
                # contain the complete OAuth response. Fall through silently.
                pass

    cooldown = authenticator._get_device_code_cooldown_remaining(auth_data)
    if cooldown > 0:
        token = authenticator._wait_for_access_token(cooldown)
        if token:
            return token
    return authenticator._login_device_code()["access_token"]


def _read_metadata(provider: str) -> dict[str, Any]:
    path = _auth_path(provider)
    result: dict[str, Any] = {
        "provider": provider,
        "authenticated": False,
        "path": str(path),
        "expiresAt": None,
        "expired": None,
        "permissionsSafe": True,
    }
    try:
        if not _validate_parent_chain(
            provider,
            create=False,
            repair_permissions=False,
        ):
            return result
        fd = _open_auth_fd(path, repair_permissions=False)
        if fd is None:
            return result
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except RuntimeError:
        result["permissionsSafe"] = False
        return result
    except (OSError, json.JSONDecodeError):
        return result
    if not isinstance(raw, dict):
        return result

    expires_at = raw.get("expires_at")
    try:
        expires_at = int(float(expires_at)) if expires_at is not None else None
    except (TypeError, ValueError):
        expires_at = None
    result.update(
        authenticated=bool(raw.get("access_token") or raw.get("refresh_token")),
        expiresAt=expires_at,
        expired=(time.time() >= expires_at) if expires_at is not None else None,
        permissionsSafe=True,
    )
    return result


def login(provider: str, force: bool, no_browser: bool) -> dict[str, Any]:
    path = _auth_path(provider)
    _secure_parent(path)
    _secure_auth_file(path)
    if provider == "chatgpt":
        print(
            "Experimental: LiteLLM uses the ChatGPT Codex subscription backend; "
            "OpenAI does not document it as a general Claude Code gateway contract.",
            file=sys.stderr,
        )
        if no_browser:
            print("ChatGPT OAuth uses a device code; complete it in any browser.", file=sys.stderr)
        from litellm.llms.chatgpt.authenticator import Authenticator

        if force and path.exists():
            path.unlink()
        authenticator = Authenticator()
        _install_safe_chatgpt_file_io(authenticator, path)
        _safe_chatgpt_login(authenticator)
    else:
        from litellm.llms.xai.oauth import XAIOAuthAuthenticator

        XAIOAuthAuthenticator().login(force=force, no_browser=no_browser)
    _secure_auth_file(path)
    result = _read_metadata(provider)
    if not result["authenticated"]:
        raise RuntimeError(f"{provider} OAuth completed without a stored credential")
    return result


def logout(provider: str) -> dict[str, Any]:
    path = _auth_path(provider)
    removed = False
    _secure_parent(path)
    _secure_auth_file(path)
    if path.exists():
        path.unlink()
        removed = True
    return {"provider": provider, "removed": removed, "path": str(path)}


def prepare() -> None:
    """Validate and create the managed auth layout before the shell takes its lock."""
    for provider in PROVIDERS:
        _secure_parent(_auth_path(provider))


def _emit(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    rows = payload if isinstance(payload, list) else [payload]
    for row in rows:
        provider = row["provider"]
        if "removed" in row:
            print(f"{provider}: {'logged out' if row['removed'] else 'already logged out'}")
        else:
            state = "authenticated" if row["authenticated"] else "not authenticated"
            suffix = "" if row.get("permissionsSafe", True) else " (unsafe file permissions)"
            print(f"{provider}: {state}{suffix}")


def _safe_error(provider: str, exc: Exception) -> str:
    """Return an actionable error without reflecting provider token payloads."""
    error_type = type(exc).__name__
    if provider not in PROVIDERS:
        return (
            f"OAuth storage error ({error_type}): managed credential path validation failed. "
            "Restore the package-owned state layout or reinstall claude-litellm."
        )
    return (
        f"OAuth error ({error_type}): {provider} authentication failed. "
        f"Run `claude-litellm auth login {provider} --force` to retry."
    )


def main() -> int:
    os.umask(0o077)
    # Private shell handshake: validate/create the managed directory chain
    # before the wrapper places its mutation lock inside AUTH_HOME.
    if sys.argv[1:] == ["--prepare-storage"]:
        try:
            prepare()
            return 0
        except Exception as exc:
            print(_safe_error("storage", exc), file=sys.stderr)
            return 1

    parser = argparse.ArgumentParser(prog="claude-litellm auth")
    sub = parser.add_subparsers(dest="command", required=True)

    login_parser = sub.add_parser("login")
    login_parser.add_argument("provider", choices=PROVIDERS)
    login_parser.add_argument("--force", action="store_true")
    login_parser.add_argument("--no-browser", action="store_true")
    login_parser.add_argument("--json", action="store_true")

    status_parser = sub.add_parser("status")
    status_parser.add_argument("provider", choices=(*PROVIDERS, "all"), nargs="?", default="all")
    status_parser.add_argument("--json", action="store_true")

    logout_parser = sub.add_parser("logout")
    logout_parser.add_argument("provider", choices=PROVIDERS)
    logout_parser.add_argument("--json", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "login":
            if args.json:
                # LiteLLM's device-flow helpers print browser instructions to
                # stdout. Keep stdout machine-readable without hiding those
                # instructions from the user.
                with contextlib.redirect_stdout(sys.stderr):
                    payload = login(args.provider, args.force, args.no_browser)
            else:
                payload = login(args.provider, args.force, args.no_browser)
        elif args.command == "logout":
            payload = logout(args.provider)
        else:
            selected = PROVIDERS if args.provider == "all" else (args.provider,)
            payload = [_read_metadata(provider) for provider in selected]
        _emit(payload, args.json)
        return 0
    except Exception as exc:
        provider = getattr(args, "provider", "provider")
        print(_safe_error(provider, exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
