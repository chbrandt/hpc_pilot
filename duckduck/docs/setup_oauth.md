# Interlink setup 1/3

In the "edge-node" deployment schema, interLink API (server) stands behind an OAuth proxy (in the edge-node).
The proxy will talk to the OIDC server (EGI Check-in or GitHub's or else) to verify the token provided by the user. 
If the (oauth) proxy accepts the token, the request is forwarded to the API server.

Hence the OAuth layer is composed by two parts:
- An OAuth server (e.g., EGI Check-in) -- where we'll connect to a client/application;
- The OAuth2-Proxy, installed in the edge-node.

## OAuth server

The communication between the K8S cluster and the HPC edge-node is authenticated
by an OAuth server.
This layer is composed by two components: (i) an OAuth/OICD server
(EGI Check-in, for instance), and (ii) an OAuth proxy.
The OAuth proxy sits on the edge node, its setup is placed in the
[Setup Edge-node](setup_edge.md) document. Here, we focus on setting up the
OAuth server/client setup, on generating a secrets/tokens to be used later
in the [K8S setup](setup_k8s.md).

### Check-in Refresh Token

When setting up the K8S' virtual node, we need to provide the refresh-token
that will allow for valid exchanges between K8S-HPC systems.

We are using Check-in's `oidc-agent` public client to generate the token.
The script `checkin_token_device.py` in this repos `utils/` directory
implements the necessary routine to create the refresh token (as well as an
access token, although we don't need it here).
The script implements a [device authorization flow](https://www.oauth.com/oauth2-servers/device-flow/).

> **Note:** > `utils/checkin_token_device.py` uses Python's
> [requests](pypi.org/project/requests) library.

The following command will create a new set of tokens in a file `tokens.json`,
just follow the instructions provided on the screen:

```bash
$ python checkin_token_device.py new --file tokens.json

(...)

Success! Received tokens:
  access_token:  eyJhbGciOiJSUzI1NiIs...
  refresh_token: eyJhbGciOiJIUzI1NiIs...
  id_token:      eyJhbGciOiJSUzI1NiIs...
  expires_in:    3600 seconds
  refresh_expires_in: 34127999 seconds

Tokens saved to: tokens.json (permissions 0600)
```

#### Refresh token script

Shell script to request a new refresh (and access) token.

Run the following in the terminal:

```bash
DEVICE_ENDPOINT="<https://aai.egi.eu/auth/realms/egi/protocol/openid-connect/auth/device>"
TOKEN_ENDPOINT="<https://aai.egi.eu/auth/realms/egi/protocol/openid-connect/token>"

resp=$(curl -sS -X POST "$DEVICE_ENDPOINT" \
-H "Content-Type: application/x-www-form-urlencoded" \
-d "client_id=oidc-agent" \
-d "scope=openid offline_access profile email")

device_code=$(echo "$resp" | jq -r .device_code)
verify_url=$(echo "$resp" | jq -r .verification_uri_complete)
interval=$(echo "$resp" | jq -r .interval)

echo ""
echo "Please visit the following URL in your browser:"
echo " $verify_url"
echo ""
echo "Waiting for you to authenticate..."
echo ""

while :; do
out=$(curl -sS -X POST "$TOKEN_ENDPOINT" \
-H "Content-Type: application/x-www-form-urlencoded" \
-d "grant_type=urn:ietf:params:oauth:grant-type:device_code" \
-d "device_code=$device_code" \
  -d "client_id=oidc-agent")
err=$(echo "$out" | jq -r .error 2>/dev/null || true)

if [ "$err" = "authorization_pending" ] || [ "$err" = "slow_down" ]; then
sleep "${interval:-5}"
else
  echo "$out" | jq .
break
fi
done
```

Go to “verify_url” in your browser and authenticate and authorize with your EGI Check-in account.

In a few seconds an output like the following should print in your terminal:

```json
{
  "access_token": "eyJhbGciOi...",
  "expires_in": 3600,
  "refresh_token": "eyJhbGciOi...",
  "refresh_expires_in": 34124629,
  "token_type": "Bearer",
  "id_token": "eyJhbGciOi...",
  "not-before-policy": 0,
  "session_state": "33fa2cee-...",
  "scope": "openid offline_access profile email"
}
```
