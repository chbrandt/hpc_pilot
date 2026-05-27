"""
lib — Pure Python programming interface for HPC Pilot.

This package provides the core business logic with no web-framework
dependency.  It can be imported directly from the Python interpreter,
a script, or used as the foundation for the api/ and app/ layers.

Modules
-------
k8s_client        Kubernetes Deployment / Namespace / Service / Ingress management
helm_client       Helm CLI wrapper (install, list, uninstall, get-values)
hpc_client        mccli / SSH wrapper for HPC node deployments
saved_deployments Per-user saved deployment configuration store (JSON files)
token_auth        EGI Check-in JWT validation and namespace derivation
"""
