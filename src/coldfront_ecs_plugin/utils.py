import logging
import re
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from ecsclient.client import Client
from ecsclient.common.exceptions import ECSClientException

from coldfront.core.resource.models import ResourceAttributeType


logger = logging.getLogger(__name__)

BYTES_PER_TB = 1024 ** 4
GB_PER_TB = 1024


def _setting(name: str, default: str = "") -> str:
    return str(getattr(settings, name, default) or default)


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_name(value: str, max_length: int = 63) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", value.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        slug = "default"
    return slug[:max_length]


@dataclass
class BucketUsage:
    bucket_name: str
    namespace: str
    total_size_bytes: int
    total_size_tb: float


class ECSResourceManager:
    """Helper for ECS operations tied to one ColdFront resource."""

    def __init__(self, resource):
        self.resource = resource
        self.url = self._resource_url()
        self.client = self.connect()

    def _resource_url(self) -> str:
        """
        Return the ECS endpoint URL from the resource's `url` attribute.
        """
        value = self.resource.get_attribute("url", expand=False, typed=False)
        if not value:
            raise ValueError(
                f'Resource {self.resource.pk} ({self.resource.name}) has no "url" attribute '
                "configured for ECS connectivity."
            )
        return str(value).rstrip("/")

    def connect(self):
        return Client(
            _setting("ECS_CLIENT_VERSION", "3"),
            username=_setting("ECS_USER"),
            password=_setting("ECS_PASS"),
            token_endpoint=f"{self.url}:4443/login",
            ecs_endpoint=f"{self.url}:4443",
        )

    def connect_to_resource(self):
        """Return a connected ECS client for this ColdFront resource."""
        return self.client

    def return_resource_replication_group(self) -> Optional[str]:
        """
        Return the replication group name specified on the resource, or None if not set.

        The resource attribute stores the replication group's name (e.g. 'us1'), not the vpool ID.
        """
        value = self.resource.get_attribute("replication_group", expand=False, typed=False)
        if value and str(value).strip():
            return str(value).strip()
        return None

    def replication_group_id_from_name(self, name: str) -> str:
        """
        Return the ECS replication group (vpool) ID for the given replication group name.

        Lists vpools from the cluster and finds the one whose name matches. The name is
        the human-readable replication group name (e.g. 'us1'), not the URN/id.
        """
        name = name.strip()
        if not name:
            raise ValueError("Replication group name cannot be empty.")
        r = self.client.replication_group.list()
        vpools = r.get("data_service_vpool") or r.get("dataServiceVpool") or []
        for vp in vpools:
            vp_name = (vp.get("name") or "").strip()
            if vp_name == name:
                vp_id = vp.get("id")
                if vp_id and str(vp_id).strip():
                    return str(vp_id).strip()
                raise ValueError(
                    f"Replication group '{name}' has no id in ECS response."
                )
        raise ValueError(
            f"Replication group '{name}' not found on ECS. "
            "Check the name or list replication groups on the cluster."
        )

    def _get_replication_group_id(self, namespace_name: str) -> str:
        """
        Return a valid ECS replication group (vpool) ID for bucket creation.

        Resolved in order:
        1. Namespace's default_data_services_vpool if the namespace exists and has one
        2. Resource attribute 'replication_group' (stored as a name) resolved to ID via the cluster
        """
        # Use namespace default if it has one
        try:
            ns = self.client.namespace.get(namespace_name)
            vpool = ns.get("default_data_services_vpool") or ns.get("defaultDataServicesVpool")
            if vpool and str(vpool).strip():
                return str(vpool).strip()
        except ECSClientException:
            pass

        # Resource attribute holds replication group name → resolve to ID
        value = self.return_resource_replication_group()
        if value:
            return self.replication_group_id_from_name(value)
        raise ValueError(
            "Could not resolve ECS replication group for bucket creation. "
            "Set the 'replication_group' resource attribute to the replication group name, "
            "or ensure the namespace has a default replication group configured."
        )

    def create_namespace(
        self,
        namespace_name: str,
        replication_group: Optional[str] = None,
        ldap_group: Optional[str] = None
    ):
        kwargs = {"name": namespace_name}
        if replication_group:
            vpool_id = self.replication_group_id_from_name(replication_group)
            kwargs["default_data_services_vpool"] = vpool_id
        if ldap_group:
            ldap_domain = self.resource.get_attribute("ldap_domain", expand=False, typed=False)
            if not ldap_domain:
                raise ValueError(
                    f"Cannot set user mapping for namespace '{namespace_name}' because the resource "
                    "has no 'ldap_domain' attribute configured."
                )
            kwargs['user_mapping'] = [{'domain': ldap_domain, 'groups': {'group': ldap_group}}]
        return self.client.namespace.create(**kwargs)

    def namespace_exists(self, namespace_name: str) -> bool:
        """
        Return True if the namespace exists on ECS, False if it does not.
        """
        try:
            self.client.namespace.get(namespace_name)
            return True
        except ECSClientException:
            # Treat ECS client exceptions as "does not exist" for this check.
            return False

    def get_namespace_quota_gb(self, namespace_name: str) -> Optional[float]:
        response = self.client.namespace.get_namespace_quota(namespace_name)
        block_size = response.get("blockSize")
        if block_size is None or block_size == -1:
            return None
        return float(block_size)

    def get_namespace_quota_tb(self, namespace_name: str) -> Optional[float]:
        quota_gb = self.get_namespace_quota_gb(namespace_name)
        if quota_gb is None:
            return None
        return quota_gb / GB_PER_TB

    def assign_quota_to_namespace(self, namespace_name: str, quota_tb: float):
        block_size_gb = int(round(float(quota_tb) * GB_PER_TB))
        notification_gb = int(max(1, round(block_size_gb * 0.9)))
        return self.client.namespace.update_namespace_quota(
            block_size=block_size_gb,
            notification_size=notification_gb,
            namespace=namespace_name,
        )

    def change_namespace_quota(self, namespace_name: str, new_quota_tb: float):
        return self.assign_quota_to_namespace(namespace_name, new_quota_tb)

    def create_bucket_for_namespace(
        self,
        namespace_name: str,
        bucket_name: str,
        block_limit_tb: Optional[float] = None,
        filesystem_enabled: bool = False,
        encryption_enabled: bool = False,
    ):
        vpool_id = self._get_replication_group_id(namespace_name)
        self.client.bucket.create(
            bucket_name=bucket_name,
            namespace=namespace_name,
            replication_group=vpool_id,
            filesystem_enabled=filesystem_enabled,
            encryption_enabled=encryption_enabled,
        )
        if block_limit_tb is not None:
            block_limit_gb = int(round(float(block_limit_tb) * GB_PER_TB))
            notification_gb = int(max(1, round(block_limit_gb * 0.9)))
            self.client.bucket.set_quota(
                bucket_name,
                namespace=namespace_name,
                block_size=block_limit_gb,
                notification_size=notification_gb,
            )
        return bucket_name

    def collect_bucket_usage_data(self, namespace_name: str, bucket_name: str) -> BucketUsage:
        billing_info = self.client.billing.get_bucket_billing_info(
            bucket_name,
            namespace_name,
            sizeunit="KB",
        )
        total_size_kb = _to_float(
            billing_info.get("total_size")
            if "total_size" in billing_info
            else billing_info.get("total_size_in_kb", 0)
        )
        total_size_bytes = int(total_size_kb * 1024)
        total_size_tb = total_size_bytes / BYTES_PER_TB
        return BucketUsage(
            bucket_name=bucket_name,
            namespace=namespace_name,
            total_size_bytes=total_size_bytes,
            total_size_tb=total_size_tb,
        )

    def update_resource_usage(self):
        """Update the matching ColdFront resource with ECS usage/capacity values."""
        try:
            r = self.client.capacity.get_cluster_capacity()
        except ECSClientException:
            logger.debug("Could not get ECS cluster capacity", exc_info=True)
            raise
        # capacity_tb
        provisioned_gb = _to_float(r.get("totalProvisioned_gb") or r.get("totalProvisionedGb"), 0.0)
        capacity_tb = provisioned_gb / GB_PER_TB
        self._set_resource_attribute("capacity_tb", capacity_tb)
        # used_tb
        free_gb = _to_float(r.get("totalFree_gb") or r.get("totalFreeGb"), 0.0)
        used_gb = provisioned_gb - free_gb
        used_tb = used_gb / GB_PER_TB
        self._set_resource_attribute("used_tb", used_tb)
        # allocated_tb
        allocated_tb = self.sum_namespace_quotas_tb()
        self._set_resource_attribute("allocated_tb", allocated_tb)
        result = {"allocated_tb": allocated_tb, 'capacity_tb': capacity_tb, 'used_tb': used_tb}
        return result

    def list_namespaces(self) -> list[str]:
        response = self.client.namespace.list()
        namespaces = response.get("namespace", [])
        return [entry.get("name") for entry in namespaces if entry.get("name")]

    def list_namespace_buckets(self, namespace_name: str) -> list[str]:
        response = self.client.bucket.list(namespace_name)
        buckets = response.get("object_bucket", [])
        return [entry.get("name") for entry in buckets if entry.get("name")]

    def sum_namespace_quotas_tb(self) -> float:
        total = 0.0
        for namespace in self.list_namespaces():
            quota_tb = self.get_namespace_quota_tb(namespace)
            if quota_tb is not None and quota_tb >= 0:
                total += quota_tb
        return total

    def sum_all_bucket_usage_tb(self) -> float:
        total = 0.0
        for namespace in self.list_namespaces():
            for bucket in self.list_namespace_buckets(namespace):
                try:
                    total += self.collect_bucket_usage_data(namespace, bucket).total_size_tb
                except Exception:
                    logger.exception(
                        "Could not collect usage for namespace=%s bucket=%s",
                        namespace,
                        bucket,
                    )
        return total

    def default_namespace_for_allocation(self, allocation) -> str:
        namespace = allocation.get_attribute("Namespace", expand=False, typed=False)
        if namespace:
            return str(namespace)
        return _safe_name(allocation.project.title)

    def default_bucket_for_allocation(self, allocation, namespace_name: str) -> str:
        bucket = allocation.get_attribute("Bucket", expand=False, typed=False)
        if bucket:
            return str(bucket)
        return _safe_name(f"lab-{namespace_name}-bucket")

    def _set_resource_attribute(self, attribute_name: str, value: float):
        attr_type = ResourceAttributeType.objects.get(name=attribute_name)
        attr, _ = self.resource.resourceattribute_set.get_or_create(
            resource_attribute_type=attr_type,
            defaults={"value": str(value)},
        )
        attr.value = str(value)
        attr.save()
