## InterLink setup 2/3: HPC Edge Node

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
- [Singularity][] installed in the worker nodes

[Singularity]: https://docs.sylabs.io/guides/4.3/user-guide/quick_start.html

### OAuth Proxy

[oauth2-proxy]: https://github.com/oauth2-proxy/oauth2-proxy

The OAuth proxy is put in front of the interLink API to validate/authorize the
requests received. We use [oauth2-proxy][], it gates the requests based
on JWT access tokens. The proxy uses a SSL certificate to serve HTTPS requests.

In the edge-node, user account `interlink`:

1. Let's start by declaring some variables to help us along:

   ```bash
   IL_PUBLIC_IP='<edge node public IP>'
   IL_PUBLIC_PORT='33333'
   IL_DIR="$HOME/.interlink"
   IL_DIR_BIN="$IL_DIR/bin"
   IL_DIR_LOGS="$IL_DIR/logs"
   IL_DIR_CONFIG="$IL_DIR/config"
   IL_SOCKET="${IL_DIR}/.interlink.sock"
   IL_SOCKET_PG="${IL_DIR}/.plugin.sock"
   IL_CHECKIN_SUB='<check-in user sub>'
   ```

1. Create a set of directories where everything will the set:

   ```bash
   mkdir -p $IL_DIR_BIN
   mkdir -p $IL_DIR_CONFIG
   mkdir -p $IL_DIR_LOGS
   ```

1. Create the SSL certificate/key:

   ```bash
   openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
     -keyout ${IL_DIR_CONFIG}/tls.key \
     -out ${IL_DIR_CONFIG}/tls.crt \
     -subj "/CN=interlink.demo" \
     -addext "subjectAltName=IP:${IL_PUBLIC_IP}"
   ```

1. Download [oauth2-proxy][]:

   ```bash
   curl --fail -L -o ${IL_DIR_BIN}/oauth2-proxy \
     https://github.com/dciangot/oauth2-proxy/releases/download/v0.0.3/oauth2-proxy_Linux_amd64
   chmod +x ${IL_DIR_BIN}/oauth2-proxy
   ```

1. Run the proxy:

   ```bash
   ${IL_DIR_BIN}/oauth2-proxy \
       --upstream unix://$IL_SOCKET \
       --https-address 0.0.0.0:${IL_PUBLIC_PORT} \
       --tls-cert-file ${IL_DIR_CONFIG}/tls.crt \
       --tls-key-file ${IL_DIR_CONFIG}/tls.key \
       --allowed-group $IL_CHECKIN_SUB \
       --client-id oidc-agent \
       --client-secret "\"\"" \
       --provider oidc \
       --oidc-groups-claim sub \
       --oidc-audience-claim azp \
       --oidc-extra-audience oidc-agent \
       --oidc-issuer-url 'https://aai.egi.eu/auth/realms/egi' \
       --validate-url 'https://aai.egi.eu/auth/realms/egi/protocol/openid-connect/token' \
       --cookie-secret 'RANDOM_VALUES_FOR_SESSION_SECRET' \
       --redirect-url 'http://localhost:8081' \
       --pass-authorization-header true \
       --skip-auth-route="*='*'" \
       --email-domain='*' \
       --force-https \
       --tls-cipher-suite=TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA,TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA,TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA,TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256,TLS_RSA_WITH_AES_128_CBC_SHA,TLS_RSA_WITH_AES_128_GCM_SHA256,TLS_RSA_WITH_AES_256_CBC_SHA,TLS_RSA_WITH_AES_256_GCM_SHA384 \
       --skip-jwt-bearer-tokens true \
       >${IL_DIR_LOGS}/oauth2-proxy.log 2>&1 &

   # If you like to keep the PIDs at hand:
   echo $! >${IL_DIR}/oauth2-proxy.pid
   ```

### InterLink Server

The interLink service is composed by two parts, the API and the plugin.
The API is a thin layer responsible to get the requests coming from outside
(and already approved by the OAuth proxy) and forward it to the plugin.

#### API

1. Download interLink API server:

   ```bash
   curl --fail -L -o ${IL_DIR_BIN}/interlink \
     https://github.com/interlink-hq/interLink/releases/download/0.5.1/interlink_Linux_x86_64
   chmod +x $IL_DIR_BIN/interlink
   ```

1. Create interLink config:

   ```bash
   cat <<EOF >${IL_DIR_CONFIG}/interlink.yaml
   InterlinkAddress: unix://$IL_SOCKET
   InterlinkPort: 0
   SidecarURL: unix://$IL_SOCKET_PG
   SidecarPort: 0
   VerboseLogging: false
   ErrorsOnlyLogging: false
   DataRootFolder: $IL_DIR/jobs
   EOF
   ```

1. Run interLink:

   ```bash
   INTERLINKCONFIGPATH=${IL_DIR_CONFIG}/interlink.yaml \
     ${IL_DIR_BIN}/interlink &>${IL_DIR_LOGS}/interlink.log &

   # If you like to keep the PIDs at hand:
   echo $! >${IL_DIR}/interlink.pid
   ```

#### Plugin

The plugin is the interLink component that knows about the underlying engine
to run container jobs. We are going to use a plugin that knows how to
submit and monitor Slurm jobs.

1. Download the Slurm plugin

   ```bash
   curl --fail -L -o ${IL_DIR_BIN}/plugin \
     https://github.com/interlink-hq/interlink-slurm-plugin/releases/download/0.5.2-patch1/interlink-sidecar-slurm_Linux_x86_64
   chmod +x $IL_DIR_BIN/plugin
   ```

1. Create plugin config:

   ```bash
   cat <<EOF >$IL_DIR_CONFIG/plugin.yaml
   Socket: unix://$IL_SOCKET_PG
   InterlinkPort: $IL_PUBLIC_PORT
   SidecarPort: 4000
   DataRootFolder: $IL_DIR/jobs
   VerboseLogging: false
   ErrorsOnlyLogging: false
   BashPath: /bin/bash
   SbatchPath: /usr/bin/sbatch
   ScancelPath: /usr/bin/scancel
   SqueuePath: /usr/bin/squeue
   CommandPrefix: ""
   SingularityPrefix: ""
   EOF
   ```

1. Run plugin:

   ```bash
   SLURMCONFIGPATH=$IL_DIR_CONFIG/plugin.yaml \
     $IL_DIR_BIN/plugin &> $IL_DIR_LOGS/plugin.log &

   # If you like to keep the PIDs at hand:
   echo $! >${IL_DIR}/plugin.pid
   ```

### Testing

At this point, we've finished setting up interLink in the HPC system.
We can run a simple test to see if the API and plugin are inline:

```bash
curl -v --unix-socket $IL_SOCKET http://unix/pinglink
```

and you should get something like:

```text
*   Trying /home/ubuntu/.interlink/.interlink.sock:0...
* Connected to unix (/home/ubuntu/.interlink/.interlink.sock) port 80
> GET /pinglink HTTP/1.1
> Host: unix
> User-Agent: curl/8.5.0
> Accept: */*
>
< HTTP/1.1 200 OK
< Date: Wed, 15 Oct 2025 16:19:01 GMT
< Transfer-Encoding: chunked
<
PARTITION AVAIL  TIMELIMIT   NODES(A/I/O/T) NODELIST
debug*       up   infinite          0/3/0/3 vnode-[1-3]
* Connection #0 to host unix left intact
```
