"""
Kubernetes client wrapper for pod deployment.

Handles cluster connection via kubeconfig and provides methods
for namespace management, pod creation, and service exposure.
"""

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

    # ── Pod operations ────────────────────────────────────────────────

    def create_pod(
        self,
        name: str,
        image: str,
        namespace: str = "default",
        cpu_request: Optional[str] = None,
        cpu_limit: Optional[str] = None,
        mem_request: Optional[str] = None,
        mem_limit: Optional[str] = None,
        env_vars: Optional[dict[str, str]] = None,
        port: Optional[int] = None,
        command: Optional[str] = None,
    ) -> dict:
        """
        Create a pod in the cluster.

        Args:
            name: Pod name.
            image: Container image (e.g. "nginx:latest").
            namespace: Target namespace.
            cpu_request: CPU request (e.g. "100m").
            cpu_limit: CPU limit (e.g. "500m").
            mem_request: Memory request (e.g. "64Mi").
            mem_limit: Memory limit (e.g. "256Mi").
            env_vars: Dict of environment variable key-value pairs.
            port: Container port to expose.
            command: Override command (shell string, split on spaces).

        Returns:
            dict with success status, pod info, and optional service info.
        """
        # Build container spec
        container = client.V1Container(
            name=name,
            image=image,
            image_pull_policy="IfNotPresent",
        )

        # Resources
        resources = {}
        requests = {}
        limits = {}
        if cpu_request:
            requests["cpu"] = cpu_request
        if mem_request:
            requests["memory"] = mem_request
        if cpu_limit:
            limits["cpu"] = cpu_limit
        if mem_limit:
            limits["memory"] = mem_limit
        if requests or limits:
            container.resources = client.V1ResourceRequirements(
                requests=requests or None,
                limits=limits or None,
            )

        # Environment variables
        if env_vars:
            container.env = [
                client.V1EnvVar(name=k, value=v) for k, v in env_vars.items()
            ]

        # Port
        if port:
            container.ports = [client.V1ContainerPort(container_port=port)]

        # Command override
        if command:
            container.command = ["/bin/sh", "-c", command]

        # Build pod spec
        pod = client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=namespace,
                labels={
                    "app": name,
                    "created-by": "hpc-pilot-webapp",
                },
            ),
            spec=client.V1PodSpec(
                containers=[container],
                restart_policy="Always",
            ),
        )

        try:
            created = self.core_v1.create_namespaced_pod(namespace=namespace, body=pod)
            result = {
                "success": True,
                "pod_name": created.metadata.name,
                "namespace": namespace,
                "image": image,
                "phase": created.status.phase or "Pending",
            }

            # If a port was specified, create a NodePort service
            if port:
                svc_result = self._create_nodeport_service(
                    name=name,
                    namespace=namespace,
                    target_port=port,
                )
                result["service"] = svc_result

            return result

        except ApiException as e:
            logger.error(f"Failed to create pod: {e}")
            error_msg = e.body if hasattr(e, "body") else str(e)
            try:
                import json

                error_body = json.loads(error_msg)
                error_msg = error_body.get("message", str(e))
            except (json.JSONDecodeError, TypeError):
                pass
            return {"success": False, "error": error_msg}

    def _create_nodeport_service(
        self,
        name: str,
        namespace: str,
        target_port: int,
    ) -> dict:
        """
        Create a NodePort service to expose a pod's port externally.

        Args:
            name: Pod name (used to derive service name and selector).
            namespace: Namespace.
            target_port: The container port to expose.

        Returns:
            dict with service details including node port.
        """
        svc_name = f"{name}-svc"

        service = client.V1Service(
            api_version="v1",
            kind="Service",
            metadata=client.V1ObjectMeta(
                name=svc_name,
                namespace=namespace,
                labels={
                    "app": name,
                    "created-by": "hpc-pilot-webapp",
                },
            ),
            spec=client.V1ServiceSpec(
                type="NodePort",
                selector={"app": name},
                ports=[
                    client.V1ServicePort(
                        port=target_port,
                        target_port=target_port,
                        protocol="TCP",
                    )
                ],
            ),
        )

        try:
            created = self.core_v1.create_namespaced_service(
                namespace=namespace, body=service
            )
            node_port = created.spec.ports[0].node_port

            # Try to get a node IP for the access URL
            node_ip = self._get_node_ip()

            result = {
                "success": True,
                "service_name": svc_name,
                "node_port": node_port,
                "target_port": target_port,
            }
            if node_ip:
                result["external_url"] = f"http://{node_ip}:{node_port}"
            else:
                result["external_url"] = f"http://<node-ip>:{node_port}"

            return result

        except ApiException as e:
            logger.error(f"Failed to create service: {e}")
            return {"success": False, "error": str(e)}

    def _get_node_ip(self) -> Optional[str]:
        """Try to get an external or internal IP of a cluster node."""
        try:
            nodes = self.core_v1.list_node()
            if not nodes.items:
                return None
            # Prefer ExternalIP, fall back to InternalIP
            for addr in nodes.items[0].status.addresses:
                if addr.type == "ExternalIP":
                    return addr.address
            for addr in nodes.items[0].status.addresses:
                if addr.type == "InternalIP":
                    return addr.address
        except ApiException:
            pass
        return None

    # ── Pod listing / status ──────────────────────────────────────────

    def list_pods(self, namespace: Optional[str] = None) -> list[dict]:
        """
        List pods, optionally filtered by namespace.

        Args:
            namespace: If set, list pods in this namespace only.
                Otherwise list pods across all namespaces.

        Returns:
            List of pod info dicts.
        """
        try:
            if namespace and namespace != "__all__":
                pods = self.core_v1.list_namespaced_pod(
                    namespace=namespace,
                    label_selector="created-by=hpc-pilot-webapp",
                )
            else:
                pods = self.core_v1.list_pod_for_all_namespaces(
                    label_selector="created-by=hpc-pilot-webapp",
                )

            result = []
            for pod in pods.items:
                pod_info = {
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "image": pod.spec.containers[0].image
                    if pod.spec.containers
                    else "?",
                    "phase": pod.status.phase or "Unknown",
                    "created": pod.metadata.creation_timestamp.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    if pod.metadata.creation_timestamp
                    else "?",
                }

                # Check for associated service
                try:
                    svc = self.core_v1.read_namespaced_service(
                        name=f"{pod.metadata.name}-svc",
                        namespace=pod.metadata.namespace,
                    )
                    if svc.spec.ports:
                        node_port = svc.spec.ports[0].node_port
                        node_ip = self._get_node_ip()
                        pod_info["node_port"] = node_port
                        if node_ip:
                            pod_info["external_url"] = f"http://{node_ip}:{node_port}"
                except ApiException:
                    pass

                result.append(pod_info)

            return result

        except ApiException as e:
            logger.error(f"Failed to list pods: {e}")
            return []

    def get_pod_status(self, name: str, namespace: str = "default") -> dict:
        """Get detailed status for a single pod."""
        try:
            pod = self.core_v1.read_namespaced_pod(name=name, namespace=namespace)
            status = {
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "phase": pod.status.phase or "Unknown",
                "image": pod.spec.containers[0].image if pod.spec.containers else "?",
                "created": pod.metadata.creation_timestamp.strftime("%Y-%m-%d %H:%M:%S")
                if pod.metadata.creation_timestamp
                else "?",
            }

            # Container statuses
            if pod.status.container_statuses:
                cs = pod.status.container_statuses[0]
                status["ready"] = cs.ready
                status["restart_count"] = cs.restart_count
                if cs.state.running:
                    status["state"] = "Running"
                elif cs.state.waiting:
                    status["state"] = cs.state.waiting.reason or "Waiting"
                elif cs.state.terminated:
                    status["state"] = cs.state.terminated.reason or "Terminated"

            return status

        except ApiException as e:
            logger.error(f"Failed to get pod status: {e}")
            return {"error": str(e)}

    def delete_pod(self, name: str, namespace: str = "default") -> dict:
        """Delete a pod and its associated service."""
        results = {"pod": None, "service": None}

        # Delete the pod
        try:
            self.core_v1.delete_namespaced_pod(name=name, namespace=namespace)
            results["pod"] = {"success": True, "name": name}
        except ApiException as e:
            results["pod"] = {"success": False, "error": str(e)}

        # Try to delete associated service
        svc_name = f"{name}-svc"
        try:
            self.core_v1.delete_namespaced_service(name=svc_name, namespace=namespace)
            results["service"] = {"success": True, "name": svc_name}
        except ApiException:
            # Service may not exist — that's fine
            pass

        return results
