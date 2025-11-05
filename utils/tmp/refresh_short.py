#!/usr/bin/env python3

import os
import pprint
import requests
from requests.auth import HTTPBasicAuth

iam_server = 'https://aai.egi.eu/auth/realms/egi/protocol/openid-connect/token'

iam_client_id = 'oidc-agent'
iam_client_secret = None

audience = 'interlink'

assert 'IAM_REFRESH_TOKEN' in os.environ, "Set IAM_REFRESH_TOKEN env var"
iam_refresh_token = os.environ['IAM_REFRESH_TOKEN']


r = requests.post(iam_server,
                  data={
                      "audience": audience,
                      "grant_type": "refresh_token",
                      "refresh_token": iam_refresh_token
                  },
                  auth=HTTPBasicAuth(iam_client_id, iam_client_secret))

print("\nResponse code:", r.status_code)
print("Response JSON:")
print(pprint.pprint(r.json()))
