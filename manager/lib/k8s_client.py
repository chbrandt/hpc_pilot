"""
Kubernetes client wrapper for batch Job management.

Handles cluster connection via kubeconfig and provides methods for namespace
management and Job creation targeting InterLink virtual-kubelet nodes.

Pod deployments are forwarded by InterLink to HPC batch jobs; consequently,
replica counts and container ports are not supported and are intentionally
absent from the API; CPU/memory can be requested per job.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
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
        self.certificates_v1 = client.CertificatesV1Api()

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

    def delete_namespace(self, name: str) -> dict:
        """
        Delete a namespace (and, cascading, every resource inside it).

        Args:
            name: Namespace name.

        Returns:
            dict with success status and details.
        """
        try:
            self.core_v1.delete_namespace(name=name)
            logger.info(f"Deleted namespace: {name}")
            return {"success": True, "namespace": name}
        except ApiException as e:
            logger.error(f"Failed to delete namespace: {e}")
            return {"success": False, "error": str(e)}

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
        cpu: Optional[str] = None,
        memory: Optional[str] = None,
    ) -> dict:
        """
        Create a Job whose pod is scheduled on an InterLink virtual-kubelet node.

        InterLink translates the pod spec into an HPC batch job, so replica
        counts, container ports, and Ingress are not applicable and are not
        accepted as parameters; cpu/memory resources are forwarded to the
        HPC batch scheduler.

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
            cpu: CPU request/limit (e.g. "1", "500m"); defaults to "1".
            memory: Memory request/limit (e.g. "1Gi", "512Mi"); defaults to "1Gi".

        Returns:
            dict with success status and job info.
        """
        # Container resources: user-supplied cpu/memory, else safe defaults.
        resources = client.V1ResourceRequirements(
            requests={"cpu": cpu or "1", "memory": memory or "1Gi"},
            limits={"cpu": cpu or "1", "memory": memory or "1Gi"},
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

    def create_job_from_spec(
        self,
        name: str,
        spec: dict,
        namespace: str = "default",
    ) -> dict:
        """
        Create a Job from a raw pod spec dict (the `spec` field of a Pod manifest).

        The spec is used as the Job's `spec.template.spec` verbatim, so the
        caller controls containers, env, commands, resources and the
        `nodeSelector` pinning the job to an InterLink virtual-kubelet node.
        As a convenience, the InterLink toleration
        (`virtual-node.interlink/no-schedule`) is injected when the spec does
        not already carry it, so pods pinned to a tainted virtual node are
        schedulable.

        Args:
            name: Job name.
            spec: Pod spec dict (must contain at least `containers`).
            namespace: Target namespace.

        Returns:
            dict with success status and job info.
        """
        if not isinstance(spec, dict) or not spec.get("containers"):
            return {
                "success": False,
                "error": "spec must be a dict with at least one container",
            }

        pod_spec = dict(spec)
        tolerations = list(pod_spec.get("tolerations") or [])
        tolerated = any(
            isinstance(t, dict)
            and t.get("key") == "virtual-node.interlink/no-schedule"
            for t in tolerations
        )
        if not tolerated:
            tolerations.append(
                {"key": "virtual-node.interlink/no-schedule", "operator": "Exists"}
            )
        pod_spec["tolerations"] = tolerations
        pod_spec.setdefault("restartPolicy", "Never")



        labels = {"app": name, "created-by": "hpc-pilot-webapp"}
        job_body = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
            },

            "spec": {
                "backoffLimit": 0,
                "template": {
                    "metadata": {"labels": labels},
                    "spec": pod_spec,
                },
            },
        }
        try:
            created = self.batch_v1.create_namespaced_job(
                namespace=namespace, body=job_body
            )

            return {
                "success": True,
                "job_name": created.metadata.name,
                "namespace": namespace,
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

    def approve_pending_csrs(
        self,
        namespace: str,
        timeout: float = 30.0,
        poll_interval: float = 2.0,
    ) -> list[str]:
        """
        Approve pending kubelet-serving CSRs requested by the InterLink
        virtual-kubelet's ServiceAccount in *namespace*.

        The InterLink virtual-kubelet creates a ``kubernetes.io/kubelet-serving``
        CSR for its node serving certificate on startup.  Kubernetes has no
        built-in auto-approval for *serving* CSRs (only node-bootstrapping
        *client* CSRs are auto-approved), so without this the API server
        cannot fetch pod logs from the virtual node — every
        ``GET /api/jobs/<name>/output`` fails with a TLS handshake error
        ("remote error: tls: internal error").

        The manager may only approve CSRs it can attribute to a user's own
        virtual-kubelet, hence the strict matching:

        * signer name is exactly ``kubernetes.io/kubelet-serving``;
        * the CSR is still pending (no conditions yet);
        * the requesting username is a ServiceAccount of *namespace* —
          either the InterLink SA (``virtual-node-<namespace>``) or the
          manager's own in-cluster SA when running under kubeconfig-less
          local setups.

        Args:
            namespace: The user's Kubernetes namespace.
            timeout: How long to wait (seconds) for a matching CSR to appear
                (the virtual-kubelet creates it shortly after install).
            poll_interval: Seconds between CSR list attempts.

        Returns:
            The list of CSR names that were approved (possibly empty when no
            matching pending CSR was found).  Errors are logged, not raised —
            this is a best-effort self-healing helper.
        """
        deadline = time.monotonic() + timeout
        approved: list[str] = []
        sa_prefixes = (
            f"system:serviceaccount:{namespace}:virtual-node-{namespace}",
            f"system:serviceaccount:{namespace}:",
        )

        while True:
            try:
                csrs = self.certificates_v1.list_certificate_signing_request()
            except ApiException as e:
                logger.error(f"Failed to list CSRs: {e}")
                return approved

            for csr in csrs.items:
                name = csr.metadata.name
                signer = csr.spec.signer_name
                requestor = csr.spec.username or ""
                conditions = csr.status.conditions or []

                if signer != "kubernetes.io/kubelet-serving":
                    continue
                if conditions:  # already approved/denied
                    continue
                if not any(requestor.startswith(p) for p in sa_prefixes):
                    continue

                # Plain-dict body: the API server only reads metadata.name
                # and status.conditions on approval updates, and the
                # V1CertificateSigningRequest model rejects a body without
                # a (irrelevant here) spec.
                body = {
                    "metadata": {"name": name},
                    "status": {
                        "conditions": [
                            {
                                "type": "Approved",
                                "status": "True",
                                "reason": "HPCPilotAutoApprove",
                                "message": (
                                    "Automatically approved by the HPC Pilot "
                                    "manager (InterLink virtual-kubelet "
                                    "serving certificate)."
                                ),
                                "lastUpdateTime": datetime.now(
                                    tz=timezone.utc
                                ).isoformat(),
                                "lastTransitionTime": datetime.now(
                                    tz=timezone.utc
                                ).isoformat(),
                            }
                        ]
                    },
                }
                try:
                    self.certificates_v1.replace_certificate_signing_request_approval(
                        name=name, body=body
                    )
                    approved.append(name)
                    logger.info(f"Approved pending CSR '{name}' for namespace '{namespace}'")
                except ApiException as e:
                    logger.error(f"Failed to approve CSR '{name}': {e}")

            if approved or time.monotonic() >= deadline:
                return approved
            time.sleep(poll_interval)

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
            yet, surfaced by Kubernetes as a TLS handshake error; the
            manager self-heals this by approving the pending CSR and
            retrying once — see :func:`approve_pending_csrs`).
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

            # ── Read the pod log ────────────────────────────────────────
            # ``_preload_content=False`` bypasses the client's
            # deserialization: for non-JSON bodies (raw log text) it
            # str()-mangles ``bytes`` into the literal ``"b'...'"`` repr.
            # We decode the raw bytes ourselves instead.
            try:
                response = self._read_pod_log(pod_name, namespace, container_name)
            except ApiException as e:
                # ── Self-healing: pending VK serving-cert CSR ────────────
                # A 500 "remote error: tls: internal error" from the log
                # endpoint means the InterLink virtual-kubelet's serving
                # certificate CSR has not been approved — Kubernetes has
                # no auto-approval for kubelet-*serving* CSRs.  Approve it
                # and retry once (see approve_pending_csrs()).
                if e.status == 500 and "tls" in str(e).lower():
                    approved = self.approve_pending_csrs(
                        namespace=namespace, timeout=0.0
                    )
                    if approved:
                        response = self._read_pod_log(
                            pod_name, namespace, container_name
                        )
                    else:
                        raise
                else:
                    raise

            raw = getattr(response, "data", response)
            if isinstance(raw, bytes):
                content = raw.decode("utf-8", errors="replace")
            else:
                content = str(raw)

            return {"name": name, "pod": pod_name, "content": content}

        except ApiException as e:
            logger.error(f"Failed to get job output: {e}")
            return {"error": str(e)}

    def _read_pod_log(self, pod_name: str, namespace: str, container_name: str):
        """
        Fetch a container log without client-side deserialization.

        Note that HTTP errors (including the API server's 500 proxy error
        for an unreachable kubelet) still raise ``ApiException`` — the
        client raises it in ``rest.py`` regardless of ``_preload_content``.
        """
        return self.core_v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container_name,
            _preload_content=False,
        )
