"""
Kubernetes client wrapper for batch Job management.

Handles cluster connection via kubeconfig and provides methods for namespace
management and Job creation targeting InterLink virtual-kubelet nodes.

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
                Falls back to KUBECONFIG env var, then ~/.kube/config, then
                the in-cluster ServiceAccount (Helm deployment).
        """
        self.kubeconfig_path = kubeconfig_path or os.environ.get("KUBECONFIG")
        self._load_config()
        self.core_v1 = client.CoreV1Api()
        # self.apps_v1 = client.AppsV1Api()
        self.batch_v1 = client.BatchV1Api()

    def _load_config(self):
        """
        Load the Kubernetes configuration.

        Resolution order:
            1. explicit kubeconfig path (constructor arg or KUBECONFIG env var)
            2. default kubeconfig location (``~/.kube/config``)
            3. in-cluster ServiceAccount token (``load_incluster_config``)

        Raises:
            kubernetes.config.ConfigException
            If no kubeconfig is available and the process is not running
            inside a Kubernetes pod (no ServiceAccount token mounted).
        """
        try:
            if self.kubeconfig_path:
                config.load_kube_config(config_file=self.kubeconfig_path)
                logger.info(f"Loaded kubeconfig from: {self.kubeconfig_path}")
            else:
                config.load_kube_config()
                logger.info("Loaded kubeconfig from default location")
        except (config.ConfigException, FileNotFoundError) as e:
            # No usable kubeconfig — the normal situation when the manager
            # runs as a pod (Helm chart): fall back to the pod's
            # ServiceAccount token.
            logger.info(f"No kubeconfig available ({e}); trying in-cluster config")
            try:
                config.load_incluster_config()
                logger.info("Loaded in-cluster ServiceAccount configuration")
            except config.ConfigException:
                logger.error(
                    "Failed to load Kubernetes configuration: "
                    "no kubeconfig and not running in-cluster"
                )
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

    # ── Job operations ───────────────────────────────────────────────

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
        Create a Job whose pod is scheduled on an InterLink virtual-kubelet node.

        InterLink translates the pod spec into an HPC batch job, so replica
        counts, resource requests/limits, container ports, and Ingress are not
        applicable and are not accepted as parameters.

        The pod template is always pinned to the InterLink virtual-kubelet node
        identified by *node_name*:

        * ``spec.nodeSelector["kubernetes.io/hostname"]`` is set to *node_name*.
        * A toleration for ``virtual-node.interlink/no-schedule`` (operator
          ``Exists``) is added so the pod is accepted by the tainted node.

        Args:
            name: Job (and container) name.
            image: Container image (e.g. "ubuntu:22.04").
            node_name: Name of the InterLink virtual-kubelet node on which the
                pod must be scheduled (e.g. "vk-node").
            namespace: Target namespace.
            env_vars: Dict of environment variable key-value pairs.
            command: Override command (shell string, run as /bin/sh -c).

        Returns:
            dict with success status and job info.
        """
        # Define some default resources (greater the cpu/mem=1)
        resources = client.V1ResourceRequirements(
            limits={
                "cpu": "1",
                "memory": "1Gi"
            }
        )
        # Build container spec
        container = client.V1Container(
            name=name,
            image=image,
            image_pull_policy="IfNotPresent",
            resources=resources
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
                restart_policy="Never",
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

        # Job object
        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=namespace,
                labels={
                    "app": name,
                    "created-by": "hpc-pilot-webapp",
                },
            ),
            spec=client.V1JobSpec(
                template=pod_template,
                backoff_limit=0
            ),
        )

        try:
            # Make sure class initializes 'self.batch_v1 = client.BatchV1Api()'
            created = self.batch_v1.create_namespaced_job(
                namespace=namespace, body=job
            )
            return {
                "success": True,
                "job_name": created.metadata.name,
                "namespace": namespace,
                "image": image,
                "node_name": node_name,
                "status": "running",
            }
        except ApiException as e:
            logger.error(f"Failed to create job: {e}")
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
        List batch Jobs, optionally filtered by namespace.

        Args:
            namespace: If set, list jobs in this namespace only.
                       Use "__all__" or None to list across all namespaces.

        Returns:
            List of job info dicts.
        """
        try:
            if namespace and namespace != "__all__":
                jobs = self.batch_v1.list_namespaced_job(
                    namespace=namespace,
                    label_selector="created-by=hpc-pilot-webapp",
                )
            else:
                jobs = self.batch_v1.list_job_for_all_namespaces(
                    label_selector="created-by=hpc-pilot-webapp",
                )

            result = []
            for dep in jobs.items:
                node_selector = dep.spec.template.spec.node_selector or {}

                result.append(
                    {
                        "name": dep.metadata.name,
                        "namespace": dep.metadata.namespace,
                        "image": dep.spec.template.spec.containers[0].image
                        if dep.spec.template.spec.containers
                        else "?",
                        "node_name": node_selector.get("kubernetes.io/hostname"),
                        "status": self._job_status(dep),
                        "created": dep.metadata.creation_timestamp.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        if dep.metadata.creation_timestamp
                        else "?",
                    }
                )

            return result

        except ApiException as e:
            logger.error(f"Failed to list jobs: {e}")
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
            dep = self.batch_v1.read_namespaced_job(
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

    @staticmethod
    def _job_status(job) -> str:
        """
        Map a batch Job's status counters/conditions to a display status.

        A ``batch/v1`` Job exposes its progress through ``.status`` counters
        (``active``, ``succeeded``, ``failed``, ``ready``) and a ``conditions``
        list whose entries are typed ``Complete``, ``Failed``, or
        ``Suspended``.  The condition type is mapped to a display status:

        * ``Complete``   → ``succeeded``
        * ``Failed``     → ``failed``
        * ``Suspended``  → ``suspended``
        * otherwise, an active/ready pod means ``running``, else ``unknown``.
        """
        conditions = job.status.conditions or []
        condition_map = {c.type: c.status for c in conditions}
        if condition_map.get("Complete") == "True":
            return "succeeded"
        if condition_map.get("Failed") == "True":
            return "failed"
        if condition_map.get("Suspended") == "True":
            return "suspended"
        if (job.status.active or 0) > 0 or (job.status.ready or 0) > 0:
            return "running"
        return "unknown"

    def get_job_status(self, name: str, namespace: str = "default") -> dict:
        """
        Get detailed status for a single batch Job.

        A ``batch/v1`` Job exposes its progress through ``.status`` counters
        (``active``, ``succeeded``, ``failed``, ``ready``) and a ``conditions``
        list whose entries are typed ``Complete``, ``Failed``, or
        ``Suspended``.  The condition type is mapped to a display status:

        * ``Complete``   → ``succeeded``
        * ``Failed``     → ``failed``
        * ``Suspended``  → ``suspended``
        * otherwise, an active/ready pod means ``running``, else ``unknown``.
        """
        try:
            job = self.batch_v1.read_namespaced_job(
                name=name, namespace=namespace
            )
            active = job.status.active or 0
            ready = job.status.ready or 0
            succeeded = job.status.succeeded or 0
            failed = job.status.failed or 0

            return {
                "name": job.metadata.name,
                "namespace": job.metadata.namespace,
                "ready": ready,
                "active": active,
                "succeeded": succeeded,
                "failed": failed,
                "status": self._job_status(job),
                "image": job.spec.template.spec.containers[0].image
                if job.spec.template.spec.containers
                else "?",
                "created": job.metadata.creation_timestamp.strftime("%Y-%m-%d %H:%M:%S")
                if job.metadata.creation_timestamp
                else "?",
            }

        except ApiException as e:
            logger.error(f"Failed to get job status: {e}")
            return {"error": str(e)}

    def delete_job(self, name: str, namespace: str = "default") -> dict:
        """Delete a batch Job."""
        try:
            self.batch_v1.delete_namespaced_job(name=name, namespace=namespace)
            return {"job": {"success": True, "name": name}}
        except ApiException as e:
            return {"job": {"success": False, "error": str(e)}}

    # ── Job output ────────────────────────────────────────────────────

    def get_job_output(self, name: str, namespace: str = "default") -> dict:
        """
        Retrieve a job's output (stdout/stderr) via the pod log endpoint.

        Locates pods created by the batch/v1 Job (label ``job-name=<name>``)
        and reads the first pod's container log.  For InterLink-backed
        jobs the returned text mixes InterLink's own status lines (SLURM
        submission, node assignment, timing) with the actual container
        runtime's stdout/stderr — the same content ``kubectl logs`` shows.

        Args:
            name: The batch/v1 Job name.
            namespace: The Kubernetes namespace.

        Returns:
            A dict with ``name``, ``pod``, and ``content`` on success,
            or ``{"error": "..."}`` on failure (no pods found, or the
            log endpoint is unreachable — e.g. the InterLink
            virtual-kubelet's serving certificate has not been approved
            yet, surfaced by Kubernetes as a TLS handshake error).
        """
        try:
            pods = self.core_v1.list_namespaced_pod(
                namespace=namespace, label_selector=f"job-name={name}"
            )
            if not pods.items:
                return {"error": f"No pods found for job '{name}'"}

            pod = pods.items[0]
            pod_name = pod.metadata.name
            container_name = (
                pod.spec.containers[0].name if pod.spec.containers else name
            )

            content = self.core_v1.read_namespaced_pod_log(
                name=pod_name, namespace=namespace, container=container_name
            )
            return {"name": name, "pod": pod_name, "content": content}

        except ApiException as e:
            logger.error(f"Failed to get job output: {e}")
            return {"error": str(e)}
