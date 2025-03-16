#!/usr/bin/env python3
import asyncio
import json
import signal
from typing import Dict, List, Optional
from datetime import datetime
from kubernetes import client, config
from kubernetes.client import V1Pod, V1Container, V1PodSpec, V1ObjectMeta
from mcp.shared.exceptions import McpError
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel


# Resource tracking class
class ResourceTracker:
    def __init__(self, kind: str, name: str, namespace: str):
        self.kind = kind
        self.name = name
        self.namespace = namespace
        self.created_at = datetime.now()


# Kubernetes Manager
class KubernetesManager:
    def __init__(self):
        self.resources: List[ResourceTracker] = []
        config.load_kube_config()  # Load default kubeconfig
        self.core_api = client.CoreV1Api()
        self.apps_api = client.AppsV1Api()

        # Register signal handlers
        signal.signal(signal.SIGINT, lambda s, f: asyncio.create_task(self.cleanup()))
        signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(self.cleanup()))

    async def cleanup(self):
        """Clean up all tracked resources in reverse order."""
        for resource in reversed(self.resources):
            try:
                await self.delete_resource(
                    resource.kind, resource.name, resource.namespace
                )
            except Exception as e:
                print(f"Failed to delete {resource.kind} {resource.name}: {e}")
        self.resources.clear()

    def track_resource(self, kind: str, name: str, namespace: str):
        self.resources.append(ResourceTracker(kind, name, namespace))

    async def delete_resource(self, kind: str, name: str, namespace: str):
        kind = kind.lower()
        if kind == "pod":
            await asyncio.to_thread(
                self.core_api.delete_namespaced_pod, name, namespace
            )
        elif kind == "deployment":
            await asyncio.to_thread(
                self.apps_api.delete_namespaced_deployment, name, namespace
            )
        elif kind == "service":
            await asyncio.to_thread(
                self.core_api.delete_namespaced_service, name, namespace
            )
        self.resources = [
            r
            for r in self.resources
            if not (r.kind == kind and r.name == name and r.namespace == namespace)
        ]

    def get_core_api(self):
        return self.core_api

    def get_apps_api(self):
        return self.apps_api


# Container templates
container_templates: Dict[str, V1Container] = {
    "ubuntu": V1Container(
        name="main",
        image="ubuntu:latest",
        command=["/bin/bash", "-c", "sleep infinity"],
        resources=client.V1ResourceRequirements(
            limits={"cpu": "200m", "memory": "256Mi"},
            requests={"cpu": "100m", "memory": "128Mi"},
        ),
        liveness_probe=client.V1Probe(
            _exec=client.V1ExecAction(command=["cat", "/proc/1/status"]),
            initial_delay_seconds=5,
            period_seconds=10,
        ),
    ),
    "nginx": V1Container(
        name="main",
        image="nginx:latest",
        ports=[client.V1ContainerPort(container_port=80)],
        resources=client.V1ResourceRequirements(
            limits={"cpu": "200m", "memory": "256Mi"},
            requests={"cpu": "100m", "memory": "128Mi"},
        ),
        liveness_probe=client.V1Probe(
            http_get=client.V1HTTPGetAction(path="/", port=80),
            initial_delay_seconds=5,
            period_seconds=10,
        ),
        readiness_probe=client.V1Probe(
            http_get=client.V1HTTPGetAction(path="/", port=80),
            initial_delay_seconds=2,
            period_seconds=5,
        ),
    ),
    "busybox": V1Container(
        name="main",
        image="busybox:latest",
        command=["sh", "-c", "sleep infinity"],
        resources=client.V1ResourceRequirements(
            limits={"cpu": "100m", "memory": "64Mi"},
            requests={"cpu": "50m", "memory": "32Mi"},
        ),
        liveness_probe=client.V1Probe(
            _exec=client.V1ExecAction(command=["true"]),
            period_seconds=10,
        ),
    ),
    "alpine": V1Container(
        name="main",
        image="alpine:latest",
        command=["sh", "-c", "sleep infinity"],
        resources=client.V1ResourceRequirements(
            limits={"cpu": "100m", "memory": "64Mi"},
            requests={"cpu": "50m", "memory": "32Mi"},
        ),
        liveness_probe=client.V1Probe(
            _exec=client.V1ExecAction(command=["true"]),
            period_seconds=10,
        ),
    ),
}

k8s_manager = KubernetesManager()

# FastMCP Server Setup
mcp = FastMCP(name="kube-mcp")


# Define Tool Input Schemas with Pydantic
class ListPodsInput(BaseModel):
    namespace: str = "default"


class ListDeploymentsInput(BaseModel):
    namespace: str = "default"


class ListServicesInput(BaseModel):
    namespace: str = "default"


class CreatePodInput(BaseModel):
    name: str
    namespace: str
    template: str  # Will validate against container_templates keys in the tool
    command: Optional[List[str]] = None


class DeletePodInput(BaseModel):
    name: str
    namespace: str
    ignoreNotFound: bool = False


class DescribePodInput(BaseModel):
    name: str
    namespace: str


class GetLogsInput(BaseModel):
    resourceType: str
    name: Optional[str] = None
    namespace: str = "default"
    tail: Optional[int] = 100


# Define Tools
@mcp.tool()
async def list_pods(input_data: ListPodsInput):
    pods = await asyncio.to_thread(
        k8s_manager.get_core_api().list_namespaced_pod, input_data.namespace
    )
    return [
        {
            "type": "text",
            "text": json.dumps(
                {"pods": [pod.to_dict() for pod in pods.items]}, indent=2
            ),
        }
    ]


@mcp.tool()
async def list_deployments(input_data: ListDeploymentsInput):
    deployments = await asyncio.to_thread(
        k8s_manager.get_apps_api().list_namespaced_deployment, input_data.namespace
    )
    return [
        {
            "type": "text",
            "text": json.dumps(
                {"deployments": [d.to_dict() for d in deployments.items]}, indent=2
            ),
        }
    ]


@mcp.tool()
async def list_services(input_data: ListServicesInput):
    services = await asyncio.to_thread(
        k8s_manager.get_core_api().list_namespaced_service, input_data.namespace
    )
    return [
        {
            "type": "text",
            "text": json.dumps(
                {"services": [s.to_dict() for s in services.items]}, indent=2
            ),
        }
    ]


@mcp.tool()
async def list_namespaces():
    namespaces = await asyncio.to_thread(k8s_manager.get_core_api().list_namespace)
    return [
        {
            "type": "text",
            "text": json.dumps(
                {"namespaces": [n.to_dict() for n in namespaces.items]}, indent=2
            ),
        }
    ]


@mcp.tool()
async def create_pod(input_data: CreatePodInput):
    if input_data.template not in container_templates:
        raise McpError(f"Invalid template: {input_data.template}")
    container = container_templates[input_data.template]
    if input_data.command:
        container.command = input_data.command
        container.args = None
    pod = V1Pod(
        api_version="v1",
        kind="Pod",
        metadata=V1ObjectMeta(
            name=input_data.name,
            namespace=input_data.namespace,
            labels={"mcp-managed": "true", "app": input_data.name},
        ),
        spec=V1PodSpec(containers=[container]),
    )
    try:
        response = await asyncio.to_thread(
            k8s_manager.get_core_api().create_namespaced_pod, input_data.namespace, pod
        )
        k8s_manager.track_resource("Pod", input_data.name, input_data.namespace)
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {"podName": response.metadata.name, "status": "created"}, indent=2
                ),
            }
        ]
    except client.exceptions.ApiException as e:
        raise McpError(f"Failed to create pod: {e}")


@mcp.tool()
async def delete_pod(input_data: DeletePodInput):
    try:
        await asyncio.to_thread(
            k8s_manager.get_core_api().delete_namespaced_pod,
            input_data.name,
            input_data.namespace,
        )
        return [
            {
                "type": "text",
                "text": json.dumps({"success": True, "status": "deleted"}, indent=2),
            }
        ]
    except client.exceptions.ApiException as e:
        if input_data.ignoreNotFound and e.status == 404:
            return [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"success": True, "status": "not_found"}, indent=2
                    ),
                }
            ]
        raise McpError(f"Failed to delete pod: {e}")


@mcp.tool()
async def describe_pod(input_data: DescribePodInput):
    try:
        pod = await asyncio.to_thread(
            k8s_manager.get_core_api().read_namespaced_pod,
            input_data.name,
            input_data.namespace,
        )
        return [{"type": "text", "text": json.dumps(pod.to_dict(), indent=2)}]
    except client.exceptions.ApiException as e:
        if e.status == 404:
            raise McpError("Pod not found")
        raise McpError(f"Failed to describe pod: {e}")


@mcp.tool()
async def cleanup():
    await k8s_manager.cleanup()
    return [{"type": "text", "text": json.dumps({"success": True}, indent=2)}]


@mcp.tool()
async def list_nodes():
    nodes = await asyncio.to_thread(k8s_manager.get_core_api().list_node)
    return [
        {
            "type": "text",
            "text": json.dumps({"nodes": [n.to_dict() for n in nodes.items]}, indent=2),
        }
    ]


@mcp.tool()
async def get_logs(input_data: GetLogsInput):
    if input_data.resourceType != "pod" or not input_data.name:
        raise McpError("Only pod logs supported with a name")
    try:
        logs = await asyncio.to_thread(
            k8s_manager.get_core_api().read_namespaced_pod_log,
            input_data.name,
            input_data.namespace,
            tail_lines=input_data.tail,
        )
        return [
            {
                "type": "text",
                "text": json.dumps({"logs": {input_data.name: logs}}, indent=2),
            }
        ]
    except client.exceptions.ApiException as e:
        raise McpError(f"Failed to get logs: {e}")


@mcp.resource("k8s://namespaces")
async def read_namespaces():
    try:
        api_call = k8s_manager.get_core_api().list_namespace
        result = await asyncio.to_thread(api_call)
        return [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps([item.to_dict() for item in result.items], indent=2),
            }
        ]
    except McpError as e:
        raise e
    except Exception as e:
        raise McpError(f"Failed to read resource: {e}")
