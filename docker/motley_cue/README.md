# Motley-Cue Docker

Build docker container:
```bash
docker build --platform linux/amd64 -t motley_cue --no-cache . 
```

Run container:
```bash
docker run -p 8080:8080 -p 2222:22 --platform linux/amd64 --rm --name motley_cue motley_cue
```

## Access the container

If using EGI Check-in to connect, create an (oidc-agent) account (`egi-dev`):
```bash
oidc-gen --pub --iss https://aai-dev.egi.eu/auth/realms/egi --scope "openid profile email entitlements" egi-dev
```

Connect to the container through [mccli](https://pypi.org/project/mccli):
```bash
mccli --oidc egi-dev ssh -p 2222 localhost
```

