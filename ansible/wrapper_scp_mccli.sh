#!/bin/bash
# Pass all Ansible SCP arguments directly to mccli scp
exec mccli --oidc egi-dev --mc-endpoint http://161.9.255.206:8080 scp "$@"

