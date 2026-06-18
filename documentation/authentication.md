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
6. Derive Kubernetes namespace from sub claim
        │
        ▼
7. Store {token, claims, namespace} in Flask session
        │
        ▼
8. Auto-create namespace in cluster (if it doesn't exist)
        │
        ▼
9. Redirect to requested page (or /)
```

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

The `utils/checkin_token_device.py` script in this repo implements the OAuth 2.0
Device Authorization Grant:

```bash
cd utils/
pip install -r requirements.txt
python checkin_token_device.py
```

Follow the printed URL, authenticate in your browser, then copy the printed
access token.

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

After a token is cryptographically validated, `check_group_access` inspects
two JWT claims:

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

A user whose token contains
`urn:mace:egi.eu:group:vo.access.egi.eu:role=member#aai.egi.eu` in either
claim field will pass the check.

An empty list (or omitting the key entirely) disables the check — any
authenticated EGI user is allowed.

### Response on denial

| Layer | Behaviour |
|---|---|
| REST API (`require_token`) | HTTP **403 Forbidden** with JSON `{"error": "...", "code": 403}` |
| Web GUI (`/login` POST) | Flash error message, redirect back to `/login` |

---

## Helper Functions (`token_auth.py`)

| Function | Description |
|---|---|
| `validate_token(token)` | Full 6-step validation; returns claims dict or raises `ValueError` |
| `check_group_access(claims, allowed_groups)` | Verifies group membership via entitlement substring match; raises `ValueError` on denial |
| `derive_namespace(sub)` | Derives namespace string from subject claim |
| `get_session_user()` | Reads Flask session; returns `{sub, namespace, exp, iss}` or `None` |
| `require_token(f)` | Decorator: returns 401 (invalid token) or 403 (group denied) on failure |
