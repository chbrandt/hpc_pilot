"""
manager/hpc — HPC deployment component for HPC Pilot.

Provides a Flask Blueprint (``hpc_bp``) that adds routes for deploying
and managing services (wstunnel + supervisord) on a remote HPC system
via SSH using motley-cue / mccli authentication with an EGI Check-in token.
"""

from .routes import hpc_bp

__all__ = ["hpc_bp"]
