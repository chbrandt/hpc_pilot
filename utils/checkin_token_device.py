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
$ python checkin_token.py new                        # run full device flow
$ python checkin_token.py new --audience interlink   # (optional) include audience param
$ python checkin_token.py refresh --file tokens.json # refresh using saved tokens

Security
--------
The refresh token is long-lived. Keep the tokens file private (0600). Rotate when in doubt.
"""

import requests

import argparse
import json
import logging
import os
import sys
import time
# import webbrowser

from typing import Dict, Optional

logging.basicConfig(level=logging.INFO)


REALM_BASE = "https://aai.egi.eu/auth/realms/egi/protocol/openid-connect"
DEVICE_ENDPOINT = f"{REALM_BASE}/auth/device"
TOKEN_ENDPOINT = f"{REALM_BASE}/token"

DEFAULT_CLIENT_ID = "oidc-agent"
DEFAULT_SCOPE = "openid offline_access profile email"
DEFAULT_TOKENS_PATH = "tokens_egi.json"


def start_device_flow(client_id: str,
                      scope: str,
                      audience: Optional[str] = None) -> Dict:
    """
    POST the device authorization request.
    Returns JSON with device_code, user_code, verification_uri(_complete), interval, expires_in, etc.
    """
    data = {
        "client_id": client_id,
        "scope": scope,
    }

    if audience:
        data["audience"] = audience

    logging.debug(f"Device auth request data: {data!r}")

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
                        audience: Optional[str],
                        interval: int,
                        timeout_seconds: int = 300) -> Dict:
    """
    Poll the token endpoint until success or error.
    Returns token response JSON (access_token, refresh_token, id_token, ...).
    """
    began = time.time()
    current_interval = max(5, int(interval or 5))

    while True:
        if time.time() - began > timeout_seconds:
            raise TimeoutError(
                "Device code polling timed out. Start the flow again.")

        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": client_id,
        }
        if audience:
            data["audience"] = audience

        logging.debug(f"Token request data: {data!r}")

        resp = requests.post(
            TOKEN_ENDPOINT,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
            timeout=30,
        )

        # Keycloak returns 400 with a JSON body like {"error":"authorization_pending"} during polling
        if resp.status_code == 200:
            return resp.json()

        try:
            body = resp.json()
        except Exception:
            resp.raise_for_status()
            raise

        err = body.get("error")
        if err in ("authorization_pending", "slow_down"):
            # "slow_down" suggests we should wait longer before polling again
            if err == "slow_down":
                current_interval = min(current_interval + 5, 30)
            time.sleep(current_interval)
            continue
        elif err in ("access_denied", "expired_token", "invalid_grant"):
            raise RuntimeError(f"Device flow failed: {err}: {body!r}")
        else:
            # Any other error => raise with details
            raise RuntimeError(
                f"Unexpected token response ({resp.status_code}): {body!r}")


def save_tokens(tokens: Dict, path: str):
    """Save tokens as JSON with permissions 0600."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tokens, f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_tokens(path: str) -> Dict:
    with open(path, "r") as f:
        return json.load(f)


def refresh_with_rt(refresh_token: str, client_id: str, audience: Optional[str] = None) -> Dict:
    """
    Use a refresh_token to obtain new tokens.
    """
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    if audience:
        data["audience"] = audience

    logging.debug(f"Refresh token request data: {data!r}")

    resp = requests.post(
        TOKEN_ENDPOINT,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=data,
        timeout=30,
    )
    if resp.status_code != 200:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise RuntimeError(f"Refresh failed ({resp.status_code}): {body!r}")
    return resp.json()


def run_device_flow(client_id: str, scope: str, audience: Optional[str]) -> Dict:
    """ 
    Run the full device flow and return obtained tokens.
    """
    print(
        f"[1/3] Requesting device code from EGI Check-in as client_id='{client_id}' …")
    resp = start_device_flow(
        client_id=client_id, scope=scope, audience=audience)

    device_code = resp["device_code"]
    interval = int(resp.get("interval", 5))
    user_code = resp.get("user_code")
    verify_uri = resp.get("verification_uri")
    verify_uri_c = resp.get("verification_uri_complete", verify_uri)

    print("\n[2/3] Please authorize this application:")
    print(f"  User code:            {user_code}")
    print(f"  Verification URL:     {verify_uri}")
    print(f"  Or open directly:     {verify_uri_c}\n")

    # # Try to open the browser for convenience
    # try:
    #     webbrowser.open(verify_uri_c, new=2)
    # except Exception:
    #     pass

    print(
        f"[3/3] Polling token endpoint every {interval}s ... (Ctrl+C to abort)")
    tokens = poll_token_endpoint(
        device_code=device_code,
        client_id=client_id,
        audience=audience,
        interval=interval,
        timeout_seconds=900,  # 15 minutes cap
    )

    # Show a concise summary
    at = tokens.get("access_token", "")[
        :20] + "..." if tokens.get("access_token") else None
    rt = tokens.get("refresh_token", "")[
        :20] + "..." if tokens.get("refresh_token") else None
    idt = tokens.get("id_token", "")[:20] + \
        "..." if tokens.get("id_token") else None
    print("\nSuccess! Received tokens:")
    print(f"  access_token:  {at}")
    print(f"  refresh_token: {rt}")
    print(f"  id_token:      {idt}")
    print(f"  expires_in:    {tokens.get('expires_in')} seconds")
    if "refresh_expires_in" in tokens:
        print(
            f"  refresh_expires_in: {tokens.get('refresh_expires_in')} seconds")

    return tokens


def revoke_token(refresh_token: str, access_token: str, client_id: str):
    """
    Revoke a refresh token using a valid access token.
    """
    data = {
        "token": refresh_token,
        "token_type_hint": "refresh_token",
        "client_id": client_id,
    }
    resp = requests.post(
        f"{REALM_BASE}/revocation",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {access_token}",
        },
        data=data,
        timeout=30,
    )
    return resp


if __name__ == "__main__":

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
  
  # Refresh using explicit refresh token
  %(prog)s refresh --token eyJhbG...

  # Revoke using explicit refresh token
  %(prog)s revoke --token eyJhbG... --access-token eyJhbG...
""")

    subparsers = ap.add_subparsers(
        dest="action", required=True, help="Action to perform")

    # NEW subcommand
    new_parser = subparsers.add_parser(
        "new",
        help="Get new tokens via device flow",
        description="Start a new device authorization flow to obtain tokens"
    )
    new_parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID,
                            help=f"OIDC client_id (default: {DEFAULT_CLIENT_ID})")
    new_parser.add_argument("--scope", default=DEFAULT_SCOPE,
                            help=f"OIDC scopes (default: {DEFAULT_SCOPE!r})")
    new_parser.add_argument("--audience", default=None,
                            help="Optional audience parameter (filters audiences if configured)")
    new_parser.add_argument("--file", default=DEFAULT_TOKENS_PATH,
                            help=f"Where to store tokens JSON (default: {DEFAULT_TOKENS_PATH})")

    # REFRESH subcommand
    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Refresh tokens using saved file or explicit token",
        description="Refresh access token using a refresh token from file or provided directly"
    )
    refresh_parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID,
                                help=f"OIDC client_id (default: {DEFAULT_CLIENT_ID})")
    refresh_parser.add_argument("--audience", default=None,
                                help="Optional audience parameter (filters audiences if configured)")
    refresh_parser.add_argument("--file", default=DEFAULT_TOKENS_PATH,
                                help=f"Where to read/store tokens JSON (default: {DEFAULT_TOKENS_PATH})")
    refresh_parser.add_argument("--token", metavar="REFRESH_TOKEN",
                                help="Provide refresh token directly instead of reading from file")

    # REVOKE subcommand
    revoke_parser = subparsers.add_parser(
        "revoke",
        help="Revoke refresh token",
        description="Revoke refresh token using a valid access token"
    )
    revoke_parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID,
                               help=f"OIDC client_id (default: {DEFAULT_CLIENT_ID})")
    revoke_parser.add_argument("--token", metavar="REFRESH_TOKEN",
                               help="Refresh token to revoke")
    revoke_parser.add_argument("--access-token", metavar="ACCESS_TOKEN",
                               help="Valid access token to authenticate the (revoking) request")

    args = ap.parse_args()

    try:
        if args.action == 'revoke':
            if not args.token or not args.access_token:
                print("Both --token and --access-token are required for revoke action.",
                      file=sys.stderr)
                sys.exit(2)

            resp = revoke_token(
                refresh_token=args.token,
                access_token=args.access_token,
                client_id=args.client_id)

            if resp.status_code == 200:
                print("Refresh token successfully revoked.")
            else:
                print(
                    f"Failed to revoke token ({resp.status_code}): {resp.text}", file=sys.stderr)
                sys.exit(1)
            sys.exit(0)

        if args.action == 'refresh':
            if args.token:
                rt = args.token
                tokens = refresh_with_rt(
                    rt, client_id=args.client_id, audience=args.audience)
                # print("Refreshed tokens:")
                # print(json.dumps(tokens, indent=2))
            else:
                tokens = load_tokens(args.file)
                rt = tokens.get("refresh_token")
                if not rt:
                    print("No refresh_token found in the provided JSON.",
                          file=sys.stderr)
                    sys.exit(2)
                new_tokens = refresh_with_rt(
                    rt, client_id=args.client_id, audience=args.audience)
                # Keep old refresh token if server rotates differently? We overwrite with response.
                tokens.update(new_tokens)
            # save_tokens(tokens, args.file)
            print(
                f"Refreshed. New access_token expires in {tokens.get('expires_in')} seconds.")
        else:
            tokens = run_device_flow(
                client_id=args.client_id,
                scope=args.scope,
                audience=args.audience
            )

        out_path = args.file
        save_tokens(tokens, out_path)
        print(f"\nTokens saved to: {out_path} (permissions 0600)\n")

    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
