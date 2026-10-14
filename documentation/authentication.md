# Authentication

HPC Pilot uses [EGI Check-in](https://www.egi.eu/service/check-in/) access
tokens for authentication. Tokens are JWT-formatted and validated against the
issuer's public keys (JWKS) on every login.

Users can log in in two ways:

1. **Paste an access token** on the login form (see
   [Token-Paste Login Flow](#token-paste-login-flow)), or
2. click the **🎫 Log in with EGI Check-in** button, which signs them in
   through the browser with the OAuth 2.0 authorization-code flow (see
   [EGI Check-in OAuth Login](#egi-check-in-oauth-login-browser-flow));
   this option only appears when `site.oidc` is configured.

Both methods produce the same session and run identical validation,
group-access and user-bootstrap logic.

---

## Token-Paste Login Flow

```
User pastes token → POST /login
        │
        ▼
1. Extract issuer from token header (unverified peek)
        │
        ▼
2. Check issuer is in TRUSTED_ISSUERS list
        │
        ▼
3. Fetch JWKS from {issuer}/.well-known/openid-configuration → jwks_uri
   (cached for 1 hour per issuer)
        │
        ▼
4. Verify JWT signature using matching kid from JWKS
        │
        ▼
5. Verify exp (not expired) + iss (matches trusted issuer)
        │
        ▼
6. If allowed_groups is set: fetch entitlements from the UserInfo endpoint
   and run check_group_access (deny → 403-equivalent flash + redirect)
        │
        ▼
7. Derive Kubernetes namespace from sub claim
        │
        ▼
8. Store {token, claims, namespace} in Flask session
        │
        ▼
9. POST /api/userspace/  (auto-create the namespace if missing)
        │
        ▼
10. POST /api/saved/seed  (seed default chart presets for the user)
        │
        ▼
11. Redirect to requested page (or /)
```

The same validation + group check runs on every REST API request via the
`require_token` decorator (no session is used for API calls).


---

## EGI Check-in OAuth Login (browser flow)

Since v0.4 the login page can also offer a **🎫 "Log in with EGI Check-in"**
button (GA4) that signs the user in through the browser instead of asking
them to paste a token. It implements the OAuth 2.0 **authorization-code
flow** against the EGI Check-in Keycloak realm, with PKCE (RFC 7636,
S256) and CSRF `state` verification. The button only appears when an OIDC
client is configured under `site.oidc` (see
[Configuration — `site` section](configuration.md#site-section)).

### Flow

```
🎫 Click "Log in with EGI Check-in"  →  GET /login/egi
        │
        ▼
1. Read site.oidc from pilot_config.yaml
   (client_id missing → flash "not configured" → back to /login)
        │
        ▼
2. Generate a random CSRF state and a PKCE code_verifier
   (secrets.token_urlsafe); store {oidc_state, oidc_next,
   oidc_code_verifier} in the session
        │
        ▼
3. Redirect to {issuer}/protocol/openid-connect/auth
   ?client_id=...&redirect_uri=<site.hostname>/oauth/callback
   &response_type=code&scope=openid&state=...
   &code_challenge=<S256(code_verifier)>&code_challenge_method=S256
        │
        ▼
4. User authenticates at EGI Check-in (browser)
        │
        ▼
5. GET /oauth/callback?code=...&state=...&iss=...
        │
        ▼
6. Verify state against the session (CSRF) and iss against the
   configured issuer (RFC 9207) — mismatch → flash + /login
        │
        ▼
7. Exchange the code + PKCE code_verifier at
   {issuer}/protocol/openid-connect/token
   (grant_type=authorization_code; client_secret only if configured)
        │
        ▼
8. _establish_session(access_token, next) — identical to the token-paste
   login: steps 6–11 of the Token-Paste Login Flow above (JWKS validation
   → group check → namespace derivation → userspace ensure → config seeding)
        │
        ▼
9. Redirect to the requested page (or /)
```

Both login methods produce the **same session** (`token`, `claims`,
`namespace`) and run the same validation, group-access and bootstrap logic —
`_establish_session()` in `app/auth.py` is shared by `POST /login` and the
OAuth callback.

### Enabling it

1. Register an OIDC client in EGI Check-in (via the Check-in / VO client
   registration) with `https://<site.hostname>/oauth/callback` as an
   allowed redirect URI. PKCE (S256) is always used — Check-in realms
   reject authorization requests without it.
2. Configure the client in `manager/pilot_config.yaml`:

```yaml
site:
  oidc:
    client_id: hpc-pilot
    client_secret: ""           # required only for confidential clients
    issuer: https://aai.egi.eu/auth/realms/egi
    redirect_uri: ""            # optional explicit override (derived from hostname)
    # audience: ""              # optional: enforce the JWT 'aud' claim
```

| Key | Required | Description |
|---|---|---|
| `client_id` | ✓ | OIDC client registered in EGI Check-in. Unset/empty → the button is hidden and `GET /login/egi` redirects back to `/login` with an error. |
| `client_secret` | only for confidential clients | Sent with the authorization-code exchange. Prefer the `PILOT_OIDC_CLIENT_SECRET` environment variable, which overrides this value when non-empty (keeps the secret out of git). |
| `issuer` | no (defaults to `https://aai.egi.eu/auth/realms/egi`) | Keycloak realm providing the authorize/token endpoints. Must be one of the trusted issuers (see [Trusted Issuers](#trusted-issuers)). |
| `redirect_uri` | no (derived as `<site.hostname>/oauth/callback`) | Explicit override; must match a redirect URI allowed for the client. |
| `audience` | no | Optional expected JWT `aud` claim; enforced by both the GUI and the REST API (see [Token Audience](#token-audience)). Default: not checked. |

Reference: [Web UI Routes — `GET /login/egi` / `GET /oauth/callback`](api.md#authentication-routes-appauthpy)
and [Configuration — `site` section](configuration.md#site-section).

> **Known limitation:** the `charts/manager` Helm chart does not (yet)
> render an `oidc:` block into its generated `pilot_config.yaml`, so
> in-cluster deployments cannot enable the button through chart values.
> Provide the `site.oidc` section via your own config file (or extend
> [`charts/manager/templates/configmap-pilot.yaml`](../charts/manager/templates/configmap-pilot.yaml)).

---

## Trusted Issuers

Defined in `token_auth.py`:

```python
TRUSTED_ISSUERS = [
    "https://aai.egi.eu/auth/realms/egi",       # production
    "https://aai-dev.egi.eu/auth/realms/egi",    # development
    "https://aai-demo.egi.eu/auth/realms/egi",   # demo
]
```

A token from any other issuer is rejected with a `ValueError`.

---

## Token Audience

`validate_token` always verifies the signature, expiry and issuer. The
JWT `aud` (audience) claim is **not enforced by default**: access tokens
that carry an audience — e.g. tokens issued to an OAuth client through
the **Log in with EGI Check-in** browser flow — validate fine, as do
tokens without an `aud` claim at all (the classic token-portal /
`oidc-agent` paste login).

To require the claim, set the optional key in `pilot_config.yaml`; both
the web GUI and the REST API enforce it consistently:

```yaml
site:
  oidc:
    audience: hpc-pilot   # JWT `aud` must contain this value
```

To find the right value, decode an access token (paste it into
[jwt.io](https://jwt.io) and read the `aud` claim). On mismatch the
login fails with `Token audience not accepted (expected '<audience>')`.

---

## JWKS Key Caching

Keys are cached in `_KEY_CACHE` (a module-level dict) keyed by issuer URL.
Each cache entry stores the raw JWKS JSON and a timestamp. The TTL is
**3600 seconds (1 hour)**.

On a signature verification failure (e.g. key rotation), the cache for that
issuer is cleared and a single retry is attempted before raising.

```python
_KEY_CACHE: dict = {}   # { issuer: {keys: [...], fetched_at: float} }
```

---

## Namespace Derivation

Each user is assigned a deterministic, private Kubernetes namespace derived
from their `sub` (subject) claim:

```python
def derive_namespace(sub: str) -> str:
    return "user-" + hashlib.sha256(sub.encode()).hexdigest()[:16]
```

Properties:

- **Always valid**: `user-` prefix + 16 lowercase hex chars = 21 chars, matches
  RFC 1123 subdomain rules
- **Deterministic**: same `sub` always produces the same namespace
- **Private**: the 64-bit hash prefix makes namespace names unguessable
- **Collision-resistant**: SHA-256 makes deliberate collisions infeasible

Example:

```
sub = "1234@egi.eu"
namespace = "user-03ac674216f3e1"
```

---

## Session Lifecycle

| Event | Action |
|---|---|
| Successful login (either method) | `session.clear()` then store `token`, `claims`, `namespace` |
| EGI Check-in OAuth login started | `GET /login/egi` stores `oidc_state` and `oidc_next`; the callback consumes them |
| Token expired (client-side) | JS countdown redirects to `/logout?reason=expired` |
| Manual logout | `GET /logout` clears session, redirects to `/login` |
| Token refresh | `GET /login?refresh=1` shows the login form; new token overwrites session |

The session is stored in an **encrypted client-side cookie** using Flask's
default cookie-based session backend. The encryption key is `FLASK_SECRET_KEY`.

---

## Getting an EGI Check-in Token

> The steps below are only needed for the **token-paste** login. When the
> **🎫 Log in with EGI Check-in** button is configured
> ([OAuth login](#egi-check-in-oauth-login-browser-flow)), no manual token
> handling is required.

### Option 1 — EGI Check-in Token Portal

1. Go to `https://aai.egi.eu/token`
2. Log in with your institutional identity
3. Copy the access token

### Option 2 — Device Code Flow

The `manager/lib/token_checkin.py` script in this repo implements the OAuth 2.0
Device Authorization Grant against EGI Check-in (using the public client
`oidc-agent`):

```bash
python manager/lib/token_checkin.py new
# Follow the printed URL, authenticate in your browser, then read the
# access token from the printed tokens file.
```

Sub-commands: `new` (full device flow), `refresh --file tokens_egi.json`,
`revoke`. See [lib.md](lib.md#libtoken_checkin-cli-token-helper).

---

## Token Expiry UI

`base.html` embeds the token's `exp` claim as a `data-exp` attribute and runs
a JavaScript countdown every second:

- Displays remaining time as `Xm XXs` (or `Xh XXm` for > 1 hour)
- Turns **red** when < 5 minutes remain
- **Automatically redirects** to `/logout?reason=expired` when the token expires
- A **🔄 Refresh** button links to `/login?refresh=1` so the user can paste a
  new token without losing their current page context

---

## Group Access Control

Access can be restricted to users who hold at least one specific EGI VO
entitlement by setting `site.allowed_groups` in `pilot_config.yaml`.

### How it works

After a token is cryptographically validated, the manager calls
`fetch_userinfo(token, issuer)` to retrieve the user's entitlements from the
EGI Check-in **UserInfo endpoint** — entitlements are **not** carried in the JWT
itself. The returned UserInfo claims (`eduperson_entitlement` and
`entitlements`) are merged into the claims dict, and `check_group_access`
inspects both:

| Claim | Description |
|---|---|
| `eduperson_entitlement` | Full RFC-format URN list, e.g. `urn:mace:egi.eu:group:vo.access.egi.eu:role=member#aai.egi.eu` |
| `entitlements` | Alias or short-form list, same URN style |

Both fields are checked together (union). An entitlement matches when it
**contains** any of the `allowed_groups` strings as a **substring**
(case-sensitive).

### Configuration

```yaml
# pilot_config.yaml (site section)
allowed_groups:
  - "vo.access.egi.eu"
```

A user whose UserInfo contains
`urn:mace:egi.eu:group:vo.access.egi.eu:role=member#aai.egi.eu` in either
claim field will pass the check.

An empty list (or omitting the key entirely) disables the check — any
authenticated EGI user is allowed (and the UserInfo call is skipped entirely).

### Response on denial / failure

| Layer | Behaviour |
|---|---|
| REST API (`require_token`) — denied | HTTP **403 Forbidden** with JSON `{"error": "...", "code": 403}` |
| REST API (`require_token`) — UserInfo unreachable | HTTP **503** with JSON `{"error": "Could not verify group membership (UserInfo endpoint unavailable): ..."}` |
| Web GUI (`/login` POST) | Flash error message, redirect back to `/login` |

---

## Helper Functions (`token_auth.py`)

| Function | Description |
|---|---|
| `validate_token(token, audience=None)` | Full validation; returns claims dict or raises `ValueError`. The `aud` claim is only checked when *audience* is given — see [Token Audience](#token-audience) |
| `fetch_userinfo(token, issuer)` | Fetch entitlements from the issuer's UserInfo endpoint; returns a claims dict |
| `check_group_access(claims, allowed_groups)` | Verifies group membership via entitlement substring match; raises `ValueError` on denial |
| `derive_namespace(sub)` | Derives namespace string from subject claim |
| `get_session_user()` | Reads Flask session; returns `{sub, namespace, exp, iss}` or `None` |
| `require_token(f)` | (in `api/auth.py`) Decorator: 401 (invalid token), 403 (group denied) or 503 (UserInfo unreachable) on failure |
