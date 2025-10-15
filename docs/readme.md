# InterLink’s Edge Node

Among interLink deployment possibilities, our use case is called the Edge node deployment: the HPC system is accessible through an edge-node where interLink API server will be deployed to serve the requests from the (elsewhere) K8S cluster.

Remarks & assumptions:

- The HPC system submits jobs using SLURM.
- The HPC's _edge-node_ can submit jobs (through Slurm).
- AuthN/AuthZ is done by an external OAuth/OIDC server, EGI Check-in in our case.

## Computing resources

We have two (virtual) clusters for this experiment:

- an HPC cluster (deployed in Tubitak):

  - IP: 161.9.255.143
  - Sudo user: cloudadm
  - Slurm user: ubuntu
  - InterLink port: 33333

- a K8S cluster (deployed in Tubitak):
  - IP:
  - Sudo user: cloudadm

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

## HPC

On the HPC side, in the edge-node, we need to set (i) an OAuth proxy,
(ii) the interLink API, and (iii) the interLink SLURM plugin.

Requirements:

- a user account which will run/deploy interLink server/components
  - for simplicity, let's use `interlink` for username
- the user account -- `interlink` -- is allowed to submit Slurm jobs
- there is a shared filesystem/mounting point between the worker nodes
  and the edge-node
  - for simplicity, I'll assume the $HOME directory is being shared.
- a network port is open for external communication with the Proxy/API
  - e.g., port `33333`

### OAuth Proxy

[oauth2-proxy]: https://github.com/oauth2-proxy/oauth2-proxy

The OAuth proxy is put in front of the interLink API to validate/authorize the
requests received. We use [oauth2-proxy][], it gates the requests based
on JWT access tokens. The proxy uses a SSL certificate to serve HTTPS requests.

In the edge-node, user account `interlink`:

1. Let's start by declaring some variables to help us along:

   ```bash
   PUBLIC_IP='<edge-node public IP>'
   PUBLIC_PORT='33333'
   DIR_IL="$HOME/.interlink"
   DIR_BIN="$DIR_IL/bin"
   DIR_LOGS="$DIR_IL/logs"
   DIR_CONFIG="$DIR_IL/config"
   SOCKET_IL="unix://${DIR_IL}/.interlink.sock"
   SOCKET_PG="unix://${DIR_IL}/.plugin.sock"
   CHECKIN_SUB='<check-in user sub>'
   ```

1. Create a set of directories where everything will the set:

   ```bash
   mkdir -p $DIR_BIN
   mkdir -p $DIR_CONFIG
   ```

1. Create the SSL certificate/key:

   ```
   openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
     -keyout ${DIR_CONFIG}/tls.key \
     -out ${DIR_CONFGI}/tls.crt \
     -subj "/CN=interlink.demo" \
     -addext "subjectAltName=IP:${PUBLIC_IP}"
   ```

1. Download [oauth2-proxy][]:

   ```bash
   curl --fail -L -o ${DIR_BIN}/oauth2-proxy \
     https://github.com/dciangot/oauth2-proxy/releases/download/v0.0.3/oauth2-proxy_Linux_amd64
   chmod +x ${DIR_BIN}/oauth2-proxy
   ```

1. Run the proxy:

   ```bash
   ${DIR_BIN}/oauth2-proxy \
       --upstream $SOCKET_IL \
       --https-address 0.0.0.0:${PUBLIC_PORT} \
       --tls-cert-file ${DIR_CONFIG}/tls.crt \
       --tls-key-file ${DIR_CONFIG}/tls.key \
       --allowed-group ${CHECKIN_SUB} \
       --client-id "oidc-agent" \
       --client-secret "\"\"" \
       --provider oidc \
       --oidc-groups-claim sub \
       --oidc-audience-claim azp \
       --oidc-extra-audience oidc-agent \
       --oidc-issuer-url "https://aai.egi.eu/auth/realms/egi" \
       --validate-url https://aai.egi.eu/auth/realms/egi/protocol/openid-connect/token \
       --cookie-secret '2ISpxtx19fm7kJlhbgC4qnkuTlkGrshY82L3nfCSKy4=' \
       --redirect-url http://localhost:8081 \
       --pass-authorization-header true \
       --skip-auth-route="*='*'" \
       --email-domain=* \
       --force-https \
       --tls-cipher-suite=TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA,TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA,TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA,TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256,TLS_RSA_WITH_AES_128_CBC_SHA,TLS_RSA_WITH_AES_128_GCM_SHA256,TLS_RSA_WITH_AES_256_CBC_SHA,TLS_RSA_WITH_AES_256_GCM_SHA384 \
       --skip-jwt-bearer-tokens true \
       >${DIR_LOGS}/oauth2-proxy.log 2>&1 &
   # If you like to keep the PIDs at hand:
   echo $! >${DIR_IL}/oauth2-proxy.pid
   ```

### InterLink Server

1. Download interLink API server:

   ```bash
   curl --fail -L -o ${DIR_BIN}/interlink \
     https://github.com/interlink-hq/interLink/releases/download/0.5.1/interlink_Linux_x86_64
   chmod +x $DIR_BIN/interlink
   ```

1. Create interLink config:

   ```bash
   cat <<EOF >${DIR_CONFIG}/InterLinkConfig.yaml
   InterlinkAddress: "$SOCKET_IL"
   InterlinkPort: "0"
   SidecarURL: "$SOCKET_PG"
   SidecarPort: "0"
   VerboseLogging: false
   ErrorsOnlyLogging: false
   DataRootFolder: "$DIR_IL"
   EOF
   ```

1. Run interLink:

   ```bash
   INTERLINKCONFIGPATH=${DIR_CONFIG}/InterLinkConfig.yaml \
     ${DIR_BIN}/interlink &>${DIR_LOGS}/interlink.log &
   # If you like to keep the PIDs at hand:
   echo $! >${DIR_IL}/interlink.pid
   ```

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
