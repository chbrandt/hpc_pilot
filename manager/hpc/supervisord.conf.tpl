; supervisord.conf — HPC Pilot managed services

[supervisord]
nodaemon=false
loglevel=info
logfile_backups=3
logfile_maxbytes=10MB
; Can use "%(here)s"
logfile=%(here)s/supervisor.log
pidfile=%(here)s/supervisor.pid

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[unix_http_server]
file=%(here)s/supervisor.sock

[supervisorctl]
serverurl=unix://%(here)s/supervisor.sock

; --- wstunnel client ---
;
; Connects to the wstunnel server running in the user's K8s namespace and
; exposes a local TCP port on this HPC node that interLink can reach.

[program:wstunnel]
command=${_WSTUNNEL_BIN} client
    --http-upgrade-path-prefix '${_WSTUNNEL_SECRET}'
    -R tcp://${_WSTUNNEL_LOCAL_PORT}:localhost:${_WSTUNNEL_LOCAL_PORT}
    ws://${_WSTUNNEL_SERVER_ADDR}:${_WSTUNNEL_SERVER_PORT}
priority=1
autostart=true
autorestart=true
startretries=5
startsecs=3
stdout_logfile=%(here)s/log/wstunnel.stdout
stderr_logfile=%(here)s/log/wstunnel.stderr
