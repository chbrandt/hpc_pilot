#!/usr/bin/env bash
set -euo pipefail

VERBOSE=${VERBOSE:-0}

DEVICE_ENDPOINT="https://aai.egi.eu/auth/realms/egi/protocol/openid-connect/auth/device"
TOKEN_ENDPOINT="https://aai.egi.eu/auth/realms/egi/protocol/openid-connect/token"

FILE_OUTPUT="${1:-tokens.json}"
[ -e "$FILE_OUTPUT" -a "$#" = 0 ] && \
  { echo "$FILE_OUTPUT exists, please remove it first (or explicit as argument)"; exit 1; }

resp=$(curl -sS -X POST "$DEVICE_ENDPOINT" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=oidc-agent" \
  -d "scope=openid offline_access profile email")
  
[ "$VERBOSE" -eq 1 ] && echo "$resp" | jq .

device_code=$(echo "$resp" | jq -r .device_code)
verify_url=$(echo "$resp"  | jq -r .verification_uri_complete)
interval=$(echo "$resp"    | jq -r .interval)
 
echo ""
echo "Please visit the following URL in your browser:"
echo "    $verify_url"
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
    echo "$out" | jq . > $FILE_OUTPUT 
    [ "$VERBOSE" -eq 1 ] && cat $FILE_OUTPUT | jq . 
    echo ""
    echo "Tokens written to $FILE_OUTPUT"
    echo ""
    break
  fi
done
