"""
Kubernetes client wrapper for Deployment management.

Handles cluster connection via kubeconfig and provides methods
for namespace management, Deployment creation, service exposure, and ingress.
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

    # ── Deployment operations ─────────────────────────────────────────

    def create_deployment(
        self,
        name: str,
        image: str,
        namespace: str = "default",
        replicas: int = 1,
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
        Create a Deployment in the cluster.

        A Deployment manages a ReplicaSet which keeps the desired number of
        pod replicas running and handles self-healing and rolling updates.

        Args:
            name: Deployment (and container) name.
            image: Container image (e.g. "nginx:latest").
            namespace: Target namespace.
            replicas: Number of pod replicas (default 1).
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
            dict with success status, deployment info, service info, and optional ingress info.
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

        # Pod template used by the Deployment's ReplicaSet
        pod_template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(
                labels={
                    "app": name,
                    "created-by": "hpc-pilot-webapp",
                },
            ),
            spec=client.V1PodSpec(
                containers=[container],
            ),
        )

        # Deployment object
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
                replicas=replicas,
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
            result = {
                "success": True,
                "deployment_name": created.metadata.name,
                "namespace": namespace,
                "image": image,
                "replicas": replicas,
                "status": "progressing",
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
            logger.error(f"Failed to create deployment: {e}")
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
            name: Deployment name (used to derive service name and label selector).
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
            name: Deployment/app name (used for ingress name and labels).
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
            annotations = {
                "kubernetes.io/ingress.class": ingress_class,
            }
            # nginx ingress controller requires rewrite-target annotation
            # when routing to a backend that serves on "/"
            if ingress_class.lower() == "nginx":
                annotations["nginx.ingress.kubernetes.io/rewrite-target"] = "/"
            metadata.annotations = annotations

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

    # ── Deployment listing / status ───────────────────────────────────

    def list_deployments(self, namespace: Optional[str] = None) -> list[dict]:
        """
        List Deployments, optionally filtered by namespace.

        Args:
            namespace: If set, list deployments in this namespace only.
                       Use "__all__" or None to list across all namespaces.

        Returns:
            List of deployment info dicts.
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

                dep_info = {
                    "name": dep.metadata.name,
                    "namespace": dep.metadata.namespace,
                    "image": dep.spec.template.spec.containers[0].image
                    if dep.spec.template.spec.containers
                    else "?",
                    "replicas": desired,
                    "ready_replicas": ready,
                    "replicas_status": f"{ready}/{desired}",
                    "status": "available" if ready >= desired > 0 else "progressing",
                    "created": dep.metadata.creation_timestamp.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    if dep.metadata.creation_timestamp
                    else "?",
                    "service_ports": [],
                    "ingress_url": None,
                }

                # Fetch associated NodePort service
                try:
                    svc = self.core_v1.read_namespaced_service(
                        name=f"{dep.metadata.name}-svc",
                        namespace=dep.metadata.namespace,
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
                        dep_info["service_ports"].append(detail)
                except ApiException:
                    pass

                # Fetch associated Ingress
                try:
                    ing = self.networking_v1.read_namespaced_ingress(
                        name=f"{dep.metadata.name}-ingress",
                        namespace=dep.metadata.namespace,
                    )
                    if ing.spec.rules:
                        rule = ing.spec.rules[0]
                        host = rule.host or "<ingress-ip>"
                        path = (
                            rule.http.paths[0].path
                            if rule.http and rule.http.paths
                            else "/"
                        )
                        dep_info["ingress_url"] = f"http://{host}{path}"
                except ApiException:
                    pass

                result.append(dep_info)

            return result

        except ApiException as e:
            logger.error(f"Failed to list deployments: {e}")
            return []

    def get_deployment_status(self, name: str, namespace: str = "default") -> dict:
        """Get detailed status for a single Deployment."""
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
            logger.error(f"Failed to get deployment status: {e}")
            return {"error": str(e)}

    def delete_deployment(self, name: str, namespace: str = "default") -> dict:
        """Delete a Deployment and its associated service and ingress."""
        results = {"deployment": None, "service": None, "ingress": None}

        # Delete the deployment
        try:
            self.apps_v1.delete_namespaced_deployment(name=name, namespace=namespace)
            results["deployment"] = {"success": True, "name": name}
        except ApiException as e:
            results["deployment"] = {"success": False, "error": str(e)}

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
