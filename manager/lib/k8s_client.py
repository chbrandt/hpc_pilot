"""
Kubernetes client wrapper for Deployment management.

Handles cluster connection via kubeconfig and provides methods for namespace
management and Deployment creation targeting InterLink virtual-kubelet nodes.

Pod deployments are forwarded by InterLink to HPC batch jobs; consequently,
replica counts, CPU/memory resource requests and limits, container ports, and
Ingress resources are not supported and are intentionally absent from the API.
"""

import json
import logging
import os
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


class K8sClient:
    """Wrapper around the Kubernetes Python client."""

    def __init__(self, kubeconfig_path: Optional[str] = None):
        """
        Initialize the Kubernetes client.

        Args:
            kubeconfig_path: Path to kubeconfig file.
                Falls back to KUBECONFIG env var, then ~/.kube/config.
        """
        self.kubeconfig_path = kubeconfig_path or os.environ.get("KUBECONFIG")
        self._load_config()
        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()

    def _load_config(self):
        """Load Kubernetes configuration from kubeconfig file."""
        try:
            if self.kubeconfig_path:
                config.load_kube_config(config_file=self.kubeconfig_path)
                logger.info(f"Loaded kubeconfig from: {self.kubeconfig_path}")
            else:
                config.load_kube_config()
                logger.info("Loaded kubeconfig from default location")
        except config.ConfigException as e:
            logger.error(f"Failed to load kubeconfig: {e}")
            raise

    # ── Namespace operations ──────────────────────────────────────────

    def list_namespaces(self) -> list[str]:
        """Return a sorted list of namespace names in the cluster."""
        try:
            ns_list = self.core_v1.list_namespace()
            return sorted(ns.metadata.name for ns in ns_list.items)
        except ApiException as e:
            logger.error(f"Failed to list namespaces: {e}")
            return ["default"]

    def create_namespace(self, name: str) -> dict:
        """
        Create a new namespace.

        Args:
            name: Namespace name.

        Returns:
            dict with success status and details.
        """
        body = client.V1Namespace(
            metadata=client.V1ObjectMeta(
                name=name,
                labels={"created-by": "hpc-pilot-webapp"},
            )
        )
        try:
            self.core_v1.create_namespace(body=body)
            logger.info(f"Created namespace: {name}")
            return {"success": True, "namespace": name}
        except ApiException as e:
            if e.status == 409:
                logger.info(f"Namespace already exists: {name}")
                return {"success": True, "namespace": name, "note": "already exists"}
            logger.error(f"Failed to create namespace: {e}")
            return {"success": False, "error": str(e)}

    def namespace_exists(self, name: str) -> bool:
        """Check if a namespace exists."""
        try:
            self.core_v1.read_namespace(name=name)
            return True
        except ApiException:
            return False

    # ── InterLink node discovery ──────────────────────────────────────

    def list_interlink_nodes(self) -> list[str]:
        """
        Return sorted names of cluster nodes registered as InterLink virtual-kubelet nodes.

        A node is considered an interlink node when it carries the taint key
        ``virtual-node.interlink/no-schedule`` (regardless of value or effect).

        Returns:
            Sorted list of node names.  Returns an empty list on error.
        """
        try:
            nodes = self.core_v1.list_node()
            result = []
            for node in nodes.items:
                taints = node.spec.taints or [] if node.spec else []
                for taint in taints:
                    if taint.key == "virtual-node.interlink/no-schedule":
                        result.append(node.metadata.name)
                        break
            return sorted(result)
        except ApiException as e:
            logger.error(f"Failed to list interlink nodes: {e}")
            return []

    # ── Deployment operations ─────────────────────────────────────────

    def create_job(
        self,
        name: str,
        image: str,
        node_name: str,
        namespace: str = "default",
        env_vars: Optional[dict[str, str]] = None,
        command: Optional[str] = None,
    ) -> dict:
        """
        Create a Deployment (job) whose pod is scheduled on an InterLink virtual-kubelet node.

        InterLink translates the pod spec into an HPC batch job, so replica
        counts, resource requests/limits, container ports, and Ingress are not
        applicable and are not accepted as parameters.

        The pod template is always pinned to the InterLink virtual-kubelet node
        identified by *node_name*:

        * ``spec.nodeSelector["kubernetes.io/hostname"]`` is set to *node_name*.
        * A toleration for ``virtual-node.interlink/no-schedule`` (operator
          ``Exists``) is added so the pod is accepted by the tainted node.

        Args:
            name: Deployment (and container) name.
            image: Container image (e.g. "ubuntu:22.04").
            node_name: Name of the InterLink virtual-kubelet node on which the
                pod must be scheduled (e.g. "vk-node").
            namespace: Target namespace.
            env_vars: Dict of environment variable key-value pairs.
            command: Override command (shell string, run as /bin/sh -c).

        Returns:
            dict with success status and deployment info.
        """
        # Build container spec
        container = client.V1Container(
            name=name,
            image=image,
            image_pull_policy="IfNotPresent",
        )

        # Environment variables
        if env_vars:
            container.env = [
                client.V1EnvVar(name=k, value=v) for k, v in env_vars.items()
            ]

        # Command override
        if command:
            container.command = ["/bin/sh", "-c", command]

        # Pod template — pinned to the interlink virtual-kubelet node.
        pod_template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(
                labels={
                    "app": name,
                    "created-by": "hpc-pilot-webapp",
                },
            ),
            spec=client.V1PodSpec(
                containers=[container],
                node_selector={"kubernetes.io/hostname": node_name},
                tolerations=[
                    client.V1Toleration(
                        key="virtual-node.interlink/no-schedule",
                        operator="Exists",
                    )
                ],
            ),
        )

        # Deployment object — a single replica is always used because InterLink
        # maps one pod to one HPC job.
        deployment = client.V1Deployment(
            api_version="apps/v1",
            kind="Deployment",
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=namespace,
                labels={
                    "app": name,
                    "created-by": "hpc-pilot-webapp",
                },
            ),
            spec=client.V1DeploymentSpec(
                replicas=1,
                selector=client.V1LabelSelector(
                    match_labels={"app": name},
                ),
                template=pod_template,
            ),
        )

        try:
            created = self.apps_v1.create_namespaced_deployment(
                namespace=namespace, body=deployment
            )
            return {
                "success": True,
                "job_name": created.metadata.name,
                "namespace": namespace,
                "image": image,
                "node_name": node_name,
                "status": "progressing",
            }
        except ApiException as e:
            logger.error(f"Failed to create deployment: {e}")
            error_msg = e.body if hasattr(e, "body") else str(e)
            try:
                error_body = json.loads(error_msg)
                error_msg = error_body.get("message", str(e))
            except (json.JSONDecodeError, TypeError):
                pass
            return {"success": False, "error": error_msg}

    # ── Job listing / status ──────────────────────────────────────────

    def list_jobs(self, namespace: Optional[str] = None) -> list[dict]:
        """
        List jobs (Deployments), optionally filtered by namespace.

        Args:
            namespace: If set, list jobs in this namespace only.
                       Use "__all__" or None to list across all namespaces.

        Returns:
            List of job info dicts.
        """
        try:
            if namespace and namespace != "__all__":
                deployments = self.apps_v1.list_namespaced_deployment(
                    namespace=namespace,
                    label_selector="created-by=hpc-pilot-webapp",
                )
            else:
                deployments = self.apps_v1.list_deployment_for_all_namespaces(
                    label_selector="created-by=hpc-pilot-webapp",
                )

            result = []
            for dep in deployments.items:
                desired = dep.spec.replicas or 0
                ready = dep.status.ready_replicas or 0
                node_selector = dep.spec.template.spec.node_selector or {}

                result.append(
                    {
                        "name": dep.metadata.name,
                        "namespace": dep.metadata.namespace,
                        "image": dep.spec.template.spec.containers[0].image
                        if dep.spec.template.spec.containers
                        else "?",
                        "node_name": node_selector.get("kubernetes.io/hostname"),
                        "status": "available" if ready >= desired > 0 else "progressing",
                        "created": dep.metadata.creation_timestamp.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        if dep.metadata.creation_timestamp
                        else "?",
                    }
                )

            return result

        except ApiException as e:
            logger.error(f"Failed to list deployments: {e}")
            return []

    def get_job_spec(self, name: str, namespace: str = "default") -> dict:
        """
        Read back the full job spec from the cluster, suitable for saving
        as a reusable configuration template.

        Returns
        -------
        dict
            A dict mirroring the parameters accepted by :meth:`create_job`:
            ``name``, ``image``, ``node_name``, ``env_vars``, ``command``.
            Returns ``{"error": "..."}`` on failure.
        """
        try:
            dep = self.apps_v1.read_namespaced_deployment(
                name=name, namespace=namespace
            )
            container = (
                dep.spec.template.spec.containers[0]
                if dep.spec.template.spec.containers
                else None
            )
            if not container:
                return {"error": "No containers found in deployment spec"}

            # ── Environment variables ─────────────────────────────────
            env_vars = {}
            if container.env:
                for ev in container.env:
                    # Only capture plain key=value pairs (skip valueFrom refs)
                    if ev.value is not None:
                        env_vars[ev.name] = ev.value

            # ── Command ───────────────────────────────────────────────
            command = None
            if container.command:
                # We store as a shell command string (/bin/sh -c "…")
                if (
                    len(container.command) == 3
                    and container.command[0] == "/bin/sh"
                    and container.command[1] == "-c"
                ):
                    command = container.command[2]
                else:
                    command = " ".join(container.command)

            # ── InterLink node name ────────────────────────────────────
            node_selector = dep.spec.template.spec.node_selector or {}
            node_name = node_selector.get("kubernetes.io/hostname")

            return {
                "name": dep.metadata.name,
                "image": container.image,
                "node_name": node_name,
                "env_vars": env_vars,
                "command": command,
            }

        except ApiException as e:
            logger.error(f"Failed to get job spec: {e}")
            return {"error": str(e)}

    def get_job_status(self, name: str, namespace: str = "default") -> dict:
        """Get detailed status for a single job (Deployment)."""
        try:
            dep = self.apps_v1.read_namespaced_deployment(
                name=name, namespace=namespace
            )
            desired = dep.spec.replicas or 0
            ready = dep.status.ready_replicas or 0
            available = dep.status.available_replicas or 0
            updated = dep.status.updated_replicas or 0

            # Determine condition from status conditions
            conditions = dep.status.conditions or []
            condition_map = {c.type: c.status for c in conditions}
            if condition_map.get("Available") == "True":
                condition = "available"
            elif condition_map.get("Progressing") == "True":
                condition = "progressing"
            else:
                condition = "unknown"

            return {
                "name": dep.metadata.name,
                "namespace": dep.metadata.namespace,
                "replicas": desired,
                "ready_replicas": ready,
                "available_replicas": available,
                "updated_replicas": updated,
                "replicas_status": f"{ready}/{desired}",
                "status": condition,
                "image": dep.spec.template.spec.containers[0].image
                if dep.spec.template.spec.containers
                else "?",
                "created": dep.metadata.creation_timestamp.strftime("%Y-%m-%d %H:%M:%S")
                if dep.metadata.creation_timestamp
                else "?",
            }

        except ApiException as e:
            logger.error(f"Failed to get job status: {e}")
            return {"error": str(e)}

    def delete_job(self, name: str, namespace: str = "default") -> dict:
        """Delete a job (Deployment)."""
        try:
            self.apps_v1.delete_namespaced_deployment(name=name, namespace=namespace)
            return {"job": {"success": True, "name": name}}
        except ApiException as e:
            return {"job": {"success": False, "error": str(e)}}
