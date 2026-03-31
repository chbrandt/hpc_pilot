"""
Kubernetes client wrapper for pod deployment.

Handles cluster connection via kubeconfig and provides methods
for namespace management, pod creation, service exposure, and ingress.
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
        self.networking_v1 = client.NetworkingV1Api()

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
        ports: Optional[list[dict]] = None,
        command: Optional[str] = None,
        ingress: Optional[dict] = None,
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
            ports: List of port dicts, each with keys:
                   "number" (int), "name" (str, optional),
                   "protocol" (str, default "TCP").
            command: Override command (shell string).
            ingress: Optional dict with keys:
                     "host" (str), "path" (str),
                     "port" (int or str), "class" (str, optional).

        Returns:
            dict with success status, pod info, service info, and optional ingress info.
        """
        # Build container spec
        container = client.V1Container(
            name=name,
            image=image,
            image_pull_policy="IfNotPresent",
        )

        # Resources
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

        # Ports
        if ports:
            container.ports = [
                client.V1ContainerPort(
                    container_port=p["number"],
                    name=p.get("name") or None,
                    protocol=p.get("protocol", "TCP"),
                )
                for p in ports
            ]

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

            # If ports were specified, create a NodePort service
            if ports:
                svc_result = self._create_nodeport_service(
                    name=name,
                    namespace=namespace,
                    ports=ports,
                )
                result["service"] = svc_result

                # If ingress config was provided, create an Ingress resource
                if ingress and svc_result.get("success"):
                    ing_result = self._create_ingress(
                        name=name,
                        namespace=namespace,
                        service_name=f"{name}-svc",
                        ports=ports,
                        ingress_config=ingress,
                    )
                    result["ingress"] = ing_result

            return result

        except ApiException as e:
            logger.error(f"Failed to create pod: {e}")
            error_msg = e.body if hasattr(e, "body") else str(e)
            try:
                error_body = json.loads(error_msg)
                error_msg = error_body.get("message", str(e))
            except (json.JSONDecodeError, TypeError):
                pass
            return {"success": False, "error": error_msg}

    def _create_nodeport_service(
        self,
        name: str,
        namespace: str,
        ports: list[dict],
    ) -> dict:
        """
        Create a NodePort service exposing one or more container ports externally.

        Args:
            name: Pod name (used to derive service name and label selector).
            namespace: Namespace.
            ports: List of port dicts with "number", "name", "protocol".

        Returns:
            dict with service details, including per-port node ports.
        """
        svc_name = f"{name}-svc"

        svc_ports = []
        for p in ports:
            port_num = p["number"]
            port_name = p.get("name") or f"port-{port_num}"
            protocol = p.get("protocol", "TCP")
            svc_ports.append(
                client.V1ServicePort(
                    name=port_name,
                    port=port_num,
                    target_port=port_num,
                    protocol=protocol,
                )
            )

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
                ports=svc_ports,
            ),
        )

        try:
            created = self.core_v1.create_namespaced_service(
                namespace=namespace, body=service
            )

            node_ip = self._get_node_ip()

            # Build per-port details
            port_details = []
            for svc_port in created.spec.ports:
                detail = {
                    "name": svc_port.name,
                    "port": svc_port.port,
                    "node_port": svc_port.node_port,
                    "protocol": svc_port.protocol,
                }
                if node_ip:
                    detail["external_url"] = f"http://{node_ip}:{svc_port.node_port}"
                else:
                    detail["external_url"] = f"http://<node-ip>:{svc_port.node_port}"
                port_details.append(detail)

            return {
                "success": True,
                "service_name": svc_name,
                "node_ip": node_ip,
                "ports": port_details,
            }

        except ApiException as e:
            logger.error(f"Failed to create service: {e}")
            return {"success": False, "error": str(e)}

    def _create_ingress(
        self,
        name: str,
        namespace: str,
        service_name: str,
        ports: list[dict],
        ingress_config: dict,
    ) -> dict:
        """
        Create a Kubernetes Ingress resource to expose the service via HTTP hostname/path.

        Args:
            name: Pod/app name (used for ingress name and labels).
            namespace: Namespace.
            service_name: The backing Service name.
            ports: List of port dicts (to resolve the target port).
            ingress_config: Dict with optional keys:
                "host"  – hostname (default: "" → matches all hosts)
                "path"  – URL path prefix (default: "/")
                "port"  – target port number or name (default: first port)
                "class" – IngressClass name (default: None → cluster default)

        Returns:
            dict with ingress details.
        """
        ingress_name = f"{name}-ingress"
        host = ingress_config.get("host", "")
        path = ingress_config.get("path", "/") or "/"
        ingress_class = ingress_config.get("class") or None

        # Resolve target port
        target_port_raw = ingress_config.get("port")
        if target_port_raw:
            try:
                target_port = client.V1ServiceBackendPort(number=int(target_port_raw))
            except (ValueError, TypeError):
                # Treat as named port
                target_port = client.V1ServiceBackendPort(name=str(target_port_raw))
        else:
            # Default: first defined port
            first_port = ports[0]
            if first_port.get("name"):
                target_port = client.V1ServiceBackendPort(name=first_port["name"])
            else:
                target_port = client.V1ServiceBackendPort(number=first_port["number"])

        backend = client.V1IngressBackend(
            service=client.V1IngressServiceBackend(
                name=service_name,
                port=target_port,
            )
        )

        http_rule = client.V1HTTPIngressRuleValue(
            paths=[
                client.V1HTTPIngressPath(
                    path=path,
                    path_type="Prefix",
                    backend=backend,
                )
            ]
        )

        rule = client.V1IngressRule(
            host=host or None,
            http=http_rule,
        )

        metadata = client.V1ObjectMeta(
            name=ingress_name,
            namespace=namespace,
            labels={
                "app": name,
                "created-by": "hpc-pilot-webapp",
            },
        )
        if ingress_class:
            metadata.annotations = {
                "kubernetes.io/ingress.class": ingress_class,
            }

        spec = client.V1IngressSpec(
            rules=[rule],
        )
        if ingress_class:
            spec.ingress_class_name = ingress_class

        ingress_body = client.V1Ingress(
            api_version="networking.k8s.io/v1",
            kind="Ingress",
            metadata=metadata,
            spec=spec,
        )

        try:
            created = self.networking_v1.create_namespaced_ingress(
                namespace=namespace, body=ingress_body
            )
            url = f"http://{host}{path}" if host else f"http://<ingress-ip>{path}"
            return {
                "success": True,
                "ingress_name": ingress_name,
                "host": host or "*",
                "path": path,
                "url": url,
            }
        except ApiException as e:
            logger.error(f"Failed to create ingress: {e}")
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
                       Use "__all__" or None to list across all namespaces.

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
                    "service_ports": [],
                    "ingress_url": None,
                }

                # Fetch associated NodePort service
                try:
                    svc = self.core_v1.read_namespaced_service(
                        name=f"{pod.metadata.name}-svc",
                        namespace=pod.metadata.namespace,
                    )
                    node_ip = self._get_node_ip()
                    for svc_port in svc.spec.ports or []:
                        detail = {
                            "name": svc_port.name,
                            "port": svc_port.port,
                            "node_port": svc_port.node_port,
                        }
                        if node_ip and svc_port.node_port:
                            detail["external_url"] = (
                                f"http://{node_ip}:{svc_port.node_port}"
                            )
                        pod_info["service_ports"].append(detail)
                except ApiException:
                    pass

                # Fetch associated Ingress
                try:
                    ing = self.networking_v1.read_namespaced_ingress(
                        name=f"{pod.metadata.name}-ingress",
                        namespace=pod.metadata.namespace,
                    )
                    if ing.spec.rules:
                        rule = ing.spec.rules[0]
                        host = rule.host or "<ingress-ip>"
                        path = (
                            rule.http.paths[0].path
                            if rule.http and rule.http.paths
                            else "/"
                        )
                        pod_info["ingress_url"] = f"http://{host}{path}"
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
        """Delete a pod and its associated service and ingress."""
        results = {"pod": None, "service": None, "ingress": None}

        # Delete the pod
        try:
            self.core_v1.delete_namespaced_pod(name=name, namespace=namespace)
            results["pod"] = {"success": True, "name": name}
        except ApiException as e:
            results["pod"] = {"success": False, "error": str(e)}

        # Try to delete associated NodePort service
        svc_name = f"{name}-svc"
        try:
            self.core_v1.delete_namespaced_service(name=svc_name, namespace=namespace)
            results["service"] = {"success": True, "name": svc_name}
        except ApiException:
            pass  # Service may not exist — that's fine

        # Try to delete associated Ingress
        ingress_name = f"{name}-ingress"
        try:
            self.networking_v1.delete_namespaced_ingress(
                name=ingress_name, namespace=namespace
            )
            results["ingress"] = {"success": True, "name": ingress_name}
        except ApiException:
            pass  # Ingress may not exist — that's fine

        return results
