# Authentication

HPC Pilot uses [EGI Check-in](https://www.egi.eu/service/check-in/) access
tokens for authentication. Tokens are JWT-formatted and validated against the
issuer's public keys (JWKS) on every login.

---

## Login Flow

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
| Successful login | `session.clear()` then store `token`, `claims`, `namespace` |
| Token expired (client-side) | JS countdown redirects to `/logout?reason=expired` |
| Manual logout | `GET /logout` clears session, redirects to `/login` |
| Token refresh | `GET /login?refresh=1` shows the login form; new token overwrites session |

The session is stored in an **encrypted client-side cookie** using Flask's
default cookie-based session backend. The encryption key is `FLASK_SECRET_KEY`.

---

## Getting an EGI Check-in Token

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
`revoke`. See [lib.md](lib.md#lib-token-checkin).

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
entitlement by setting `allowed_groups` in `site_config.yaml`.

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
# manager/site_config.yaml
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
| `validate_token(token)` | Full validation; returns claims dict or raises `ValueError` |
| `fetch_userinfo(token, issuer)` | Fetch entitlements from the issuer's UserInfo endpoint; returns a claims dict |
| `check_group_access(claims, allowed_groups)` | Verifies group membership via entitlement substring match; raises `ValueError` on denial |
| `derive_namespace(sub)` | Derives namespace string from subject claim |
| `get_session_user()` | Reads Flask session; returns `{sub, namespace, exp, iss}` or `None` |
| `require_token(f)` | (in `api/auth.py`) Decorator: 401 (invalid token), 403 (group denied) or 503 (UserInfo unreachable) on failure |
