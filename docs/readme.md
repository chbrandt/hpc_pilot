# InterLink’s Edge Node

Among interLink deployment possibilities, our use case is called the Edge node deployment: the HPC system is accessible through an edge-node where interLink API server will be deployed to serve the requests from the (elsewhere) K8S cluster.

Remarks & assumptions:

- The HPC system submits jobs using SLURM.
- The HPC's _edge-node_ can submit jobs (through Slurm).
- AuthN/AuthZ is done by an external OAuth/OIDC server, EGI Check-in in our case.

## Computing resources

We have two (virtual) clusters for this experiment:

- an HPC cluster (deployed in Tubitak):

  - IP: <public-IP> 
  - Sudo user: 
  - Slurm user: 
  - InterLink port: <public-port>

- a K8S cluster (deployed in Tubitak):
  - IP:
  - Sudo user:

## Install instructions

There are three components we need to set up:

- InterLink Server: on the HPC side, in the edge-node, interLink is composed
  by three parts:

  - an OAuth proxy
  - the interLink API
  - interLink plugin

- AuthN/AuthZ: OAuth/OIDC protocol is used to authenticate and authorize the
  interaction between components. AuthN/AuthZ has the components itself:

  - an OAuth/OIDC server/client duo somewhere in the Web: EGI Check-in/`oidc-agent`, in our case
  - in the HPC: an OAuth proxy (i.e., oauth2-proxy)
  - in the K8S: a refresh token

- InterLink Node: on the K8S side, interLink comes as a _virtual node_
  deploying multiple pods:
  - refresh-token pod: in possession of a Check-in's refresh token, continuously
    renews access tokens used to communicate with interLink server.

The setup of those components will be organized under the system domain they
are deployed, in the coming sections.

- [HPC](install_hpc.md)

## Authentication & Authorization

In interLink, the API itself stands behind an OAuth proxy. The proxy will talk to the OIDC server (EGI Check-in or GitHub's or else) to verify the token provided by the user. If the (oauth) proxy accepts the token, the requested payload is forwarded to the interLink API.

So, there are two software components here:
An OAuth server/application (e.g., EGI Check-in);
The OAuth2-Proxy, installed in the edge-node.

And:
The (refresh/access) token generated through a device flow.

Refresh & Access Tokens
InterLink will keep hold of a refresh token in the K8S cluster, which will be used every “T” seconds to request a new access token to communicate with the interLink API (on the HPC cluster edge node).
To request a (new) refresh token you can use the checkin-tokens.sh script at [1] or the following commands directly.
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
