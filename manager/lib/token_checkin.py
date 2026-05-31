#!/usr/bin/env python3
"""
EGI Check-in — OAuth 2.0 Device Authorization (Device Flow) using the public client 'oidc-agent'.

What it does
------------
1) Starts the device flow (POST /device/auth) with scope "openid offline_access profile email".
2) Opens (or prints) the verification URL for you to approve in the browser.
3) Polls the token endpoint until success (access_token, refresh_token, id_token).
4) Saves tokens to a JSON file with 0600 permissions.
5) Provides a helper to refresh tokens using the stored refresh_token.

Usage
-----
$ python token_checkin.py new                        # run full device flow
$ python token_checkin.py new --audience interlink   # (optional) include audience param
$ python token_checkin.py refresh --file tokens.json # refresh using saved tokens

Security
--------
The refresh token is long-lived. Keep the tokens file private (0600). Rotate when in doubt.
"""

# stdlib
import argparse
import json
import logging
import os
import sys
import time
from typing import Dict, Optional

# third-party
import requests

logger = logging.getLogger(__name__)


REALM_BASE = "https://aai.egi.eu/auth/realms/egi/protocol/openid-connect"
DEVICE_ENDPOINT = f"{REALM_BASE}/auth/device"
TOKEN_ENDPOINT = f"{REALM_BASE}/token"
REVOCATION_ENDPOINT = f"{REALM_BASE}/revocation"

DEFAULT_CLIENT_ID = "oidc-agent"
DEFAULT_SCOPE = "openid offline_access profile email"
DEFAULT_TOKENS_PATH = "tokens_egi.json"


def start_device_flow(client_id: str,
                      scope: str,
                      audience: Optional[str] = None) -> Dict:
    """
    POST the device authorization request.

    Returns JSON with device_code, user_code, verification_uri(_complete),
    interval, expires_in, etc.

    Raises:
        requests.HTTPError: if the server returns a non-2xx status.
    """
    data: Dict[str, str] = {
        "client_id": client_id,
        "scope": scope,
    }
    if audience:
        data["audience"] = audience

    logger.debug("Device auth request data: %r", data)

    resp = requests.post(
        DEVICE_ENDPOINT,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=data,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def poll_token_endpoint(device_code: str,
                        client_id: str,
                        interval: int,
                        audience: Optional[str] = None,
                        timeout_seconds: int = 300) -> Dict:
    """
    Poll the token endpoint until success or a terminal error.

    Implements RFC 8628 §3.5 back-off: waits *interval* seconds before
    every request and increases the interval by 5 s on ``slow_down``.

    Returns:
        Token response dict (access_token, refresh_token, id_token, …).

    Raises:
        TimeoutError: if the device code expires before the user authorises.
        RuntimeError: on terminal errors (access_denied, expired_token, …).
    """
    began = time.monotonic()
    current_interval = max(5, int(interval or 5))

    while True:
        # Wait before polling (RFC 8628 §3.5 requires waiting *interval* seconds
        # between each request, including the very first one).
        time.sleep(current_interval)

        if time.monotonic() - began > timeout_seconds:
            raise TimeoutError(
                "Device code polling timed out. Start the flow again.")

        data: Dict[str, str] = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": client_id,
        }
        if audience:
            data["audience"] = audience

        logger.debug("Token request data: %r", data)

        resp = requests.post(
            TOKEN_ENDPOINT,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
            timeout=30,
        )

        # Keycloak returns 400 with a JSON body during polling.
        if resp.status_code == 200:
            return resp.json()

        try:
            body = resp.json()
        except Exception:
            resp.raise_for_status()
            raise  # unreachable but satisfies type checkers

        err = body.get("error")
        if err == "authorization_pending":
            continue
        elif err == "slow_down":
            # Server requests slower polling — increase the interval permanently.
            current_interval = min(current_interval + 5, 60)
            continue
        elif err in ("access_denied", "expired_token", "invalid_grant"):
            raise RuntimeError(f"Device flow failed: {err}: {body!r}")
        else:
            raise RuntimeError(
                f"Unexpected token response ({resp.status_code}): {body!r}")


def save_tokens(tokens: Dict, path: str) -> None:
    """Save tokens as JSON with permissions 0600 using an atomic write."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(tokens, f, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        # Clean up the temp file on failure
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_tokens(path: str) -> Dict:
    """
    Load tokens from a JSON file.

    Raises:
        FileNotFoundError: if *path* does not exist.
        ValueError: if the file is not valid JSON.
    """
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Token file not found: {path!r}. "
            "Run 'token_checkin.py new' to obtain tokens first."
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"Token file {path!r} is not valid JSON: {exc}") from exc


def refresh_with_rt(refresh_token: str,
                    client_id: str,
                    audience: Optional[str] = None) -> Dict:
    """
    Obtain new tokens by exchanging a refresh token.

    Returns:
        New token response dict.

    Raises:
        RuntimeError: if the server rejects the refresh request.
    """
    data: Dict[str, str] = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    if audience:
        data["audience"] = audience

    logger.debug("Refresh token request data: %r", data)

    resp = requests.post(
        TOKEN_ENDPOINT,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=data,
        timeout=30,
    )
    if resp.status_code != 200:
        try:
            body: object = resp.json()
        except Exception:
            body = resp.text
        raise RuntimeError(f"Refresh failed ({resp.status_code}): {body!r}")
    return resp.json()


def revoke_token(refresh_token: str, access_token: str, client_id: str) -> requests.Response:
    """
    Revoke a refresh token at the revocation endpoint.

    Parameters:
        refresh_token: The refresh token to revoke.
        access_token:  A valid access token used to authenticate the request.
        client_id:     The OIDC client_id.

    Returns:
        The raw :class:`requests.Response` (caller decides how to handle errors).
    """
    data: Dict[str, str] = {
        "token": refresh_token,
        "token_type_hint": "refresh_token",
        "client_id": client_id,
    }
    resp = requests.post(
        REVOCATION_ENDPOINT,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {access_token}",
        },
        data=data,
        timeout=30,
    )
    return resp


def run_device_flow(client_id: str,
                    scope: str,
                    audience: Optional[str]) -> Dict:
    """
    Run the full device authorization flow and return the obtained tokens.

    Raises:
        requests.HTTPError: on network errors during the device auth request.
        TimeoutError: if the user does not authorise within the expiry window.
        RuntimeError: on terminal token endpoint errors.
    """
    print(f"[1/3] Requesting device code from EGI Check-in "
          f"(client_id={client_id!r}) …")
    resp = start_device_flow(client_id=client_id, scope=scope, audience=audience)

    device_code = resp["device_code"]
    interval = int(resp.get("interval", 5))
    user_code = resp.get("user_code", "—")
    verify_uri = resp.get("verification_uri", "—")
    verify_uri_complete = resp.get("verification_uri_complete", verify_uri)

    print("\n[2/3] Please authorise this application in your browser:")
    print(f"  User code :         {user_code}")
    print(f"  Verification URL :  {verify_uri}")
    if verify_uri_complete != verify_uri:
        print(f"  Or open directly :  {verify_uri_complete}")
    print()

    print(f"[3/3] Polling token endpoint every {interval}s … (Ctrl+C to abort)")
    tokens = poll_token_endpoint(
        device_code=device_code,
        client_id=client_id,
        audience=audience,
        interval=interval,
        timeout_seconds=900,  # 15-minute cap
    )

    _print_token_summary(tokens)
    return tokens


def _print_token_summary(tokens: Dict) -> None:
    """Print a concise, truncated summary of obtained tokens."""
    def _trunc(key: str) -> str:
        val = tokens.get(key, "")
        return (val[:20] + "…") if val else "(none)"

    print("\nSuccess! Received tokens:")
    print(f"  access_token  : {_trunc('access_token')}")
    print(f"  refresh_token : {_trunc('refresh_token')}")
    print(f"  id_token      : {_trunc('id_token')}")
    print(f"  expires_in    : {tokens.get('expires_in')} s")
    if "refresh_expires_in" in tokens:
        print(f"  refresh_expires_in : {tokens['refresh_expires_in']} s")


# ── CLI entry point ────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="EGI Check-in Device Flow (public client 'oidc-agent').",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Get new tokens via device flow
  %(prog)s new
  %(prog)s new --audience interlink --file my_tokens.json

  # Refresh tokens from file
  %(prog)s refresh
  %(prog)s refresh --file my_tokens.json

  # Refresh using an explicit refresh token (saves result to --file)
  %(prog)s refresh --token eyJhbG...

  # Revoke a refresh token
  %(prog)s revoke --token eyJhbG... --access-token eyJhbG...
""",
    )

    subparsers = ap.add_subparsers(dest="action", required=True, help="Action to perform")

    # ── new ────────────────────────────────────────────────────────────────────
    new_p = subparsers.add_parser(
        "new",
        help="Get new tokens via device flow",
        description="Start a new device authorization flow to obtain tokens.",
    )
    new_p.add_argument("--client-id", default=DEFAULT_CLIENT_ID,
                       help=f"OIDC client_id (default: {DEFAULT_CLIENT_ID})")
    new_p.add_argument("--scope", default=DEFAULT_SCOPE,
                       help=f"OIDC scopes space-separated (default: {DEFAULT_SCOPE!r})")
    new_p.add_argument("--audience", default=None,
                       help="Optional audience parameter")
    new_p.add_argument("--file", default=DEFAULT_TOKENS_PATH,
                       help=f"Where to store tokens JSON (default: {DEFAULT_TOKENS_PATH})")

    # ── refresh ────────────────────────────────────────────────────────────────
    ref_p = subparsers.add_parser(
        "refresh",
        help="Refresh tokens using a saved file or an explicit refresh token",
        description="Refresh the access token using a refresh token from file or provided directly.",
    )
    ref_p.add_argument("--client-id", default=DEFAULT_CLIENT_ID,
                       help=f"OIDC client_id (default: {DEFAULT_CLIENT_ID})")
    ref_p.add_argument("--audience", default=None,
                       help="Optional audience parameter")
    ref_p.add_argument("--file", default=DEFAULT_TOKENS_PATH,
                       help=f"Token JSON file to read from / write to (default: {DEFAULT_TOKENS_PATH})")
    ref_p.add_argument("--token", metavar="REFRESH_TOKEN",
                       help="Provide the refresh token directly instead of reading from --file")

    # ── revoke ─────────────────────────────────────────────────────────────────
    rev_p = subparsers.add_parser(
        "revoke",
        help="Revoke a refresh token",
        description="Revoke a refresh token using a valid access token.",
    )
    rev_p.add_argument("--client-id", default=DEFAULT_CLIENT_ID,
                       help=f"OIDC client_id (default: {DEFAULT_CLIENT_ID})")
    rev_p.add_argument("--token", metavar="REFRESH_TOKEN", required=True,
                       help="Refresh token to revoke")
    rev_p.add_argument("--access-token", metavar="ACCESS_TOKEN", required=True,
                       help="Valid access token to authenticate the revocation request")

    return ap


def _cmd_new(args: argparse.Namespace) -> None:
    tokens = run_device_flow(
        client_id=args.client_id,
        scope=args.scope,
        audience=args.audience,
    )
    save_tokens(tokens, args.file)
    print(f"\nTokens saved to: {args.file} (permissions 0600)\n")


def _cmd_refresh(args: argparse.Namespace) -> None:
    if args.token:
        # Refresh token supplied directly on the command line.
        tokens = refresh_with_rt(
            args.token, client_id=args.client_id, audience=args.audience)
    else:
        # Read the stored token file, refresh, then merge the new tokens.
        stored = load_tokens(args.file)
        rt = stored.get("refresh_token")
        if not rt:
            print("No refresh_token found in the token file.", file=sys.stderr)
            sys.exit(2)
        new_tokens = refresh_with_rt(rt, client_id=args.client_id, audience=args.audience)
        stored.update(new_tokens)
        tokens = stored

    save_tokens(tokens, args.file)
    print(f"Refreshed. New access_token expires in {tokens.get('expires_in')} s.")
    print(f"Tokens saved to: {args.file}\n")


def _cmd_revoke(args: argparse.Namespace) -> None:
    resp = revoke_token(
        refresh_token=args.token,
        access_token=args.access_token,
        client_id=args.client_id,
    )
    if resp.status_code == 200:
        print("Refresh token successfully revoked.")
    else:
        print(f"Failed to revoke token ({resp.status_code}): {resp.text}",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    ap = _build_parser()
    args = ap.parse_args()

    try:
        if args.action == "new":
            _cmd_new(args)
        elif args.action == "refresh":
            _cmd_refresh(args)
        elif args.action == "revoke":
            _cmd_revoke(args)
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(1)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        logger.debug("Traceback:", exc_info=True)
        sys.exit(1)
