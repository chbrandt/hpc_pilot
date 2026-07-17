#!/bin/bash

echo "Generate certificate request for $1"

echo "Execute command: openssl req -new -newkey rsa:2048 -nodes -out $1.csr -keyout $1.key -subj \"/C=SK/L=Bratislava/O=Ustav informatiky SAV/CN=$1\""

openssl req -new -newkey rsa:2048 -nodes -out $1.csr -keyout $1.key \
    -subj "/C=SK/L=Bratislava/O=Ustav informatiky SAV/CN=$1"
