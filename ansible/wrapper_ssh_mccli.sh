#!/bin/bash
# Pass all Ansible SSH arguments directly to mccli ssh
exec mccli --oidc egi-dev --mc-endpoint http://161.9.255.206:8080 ssh "$@"

