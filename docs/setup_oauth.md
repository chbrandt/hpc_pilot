# Interlink setup 1/3

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
