import logging

from django.core.management.base import BaseCommand

from coldfront.core.allocation.models import (
    Allocation,
    AllocationAttribute,
    AllocationAttributeType,
)
from coldfront.core.resource.models import Resource

from coldfront_ecs_plugin.utils import BYTES_PER_TB, ECSResourceManager

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync ECS namespace quota and bucket usage into ColdFront."

    def handle(self, *args, **options):
        quota_tb_type = AllocationAttributeType.objects.get(name="Storage Quota (TB)")
        quota_bytes_type = AllocationAttributeType.objects.get(name="Quota_In_Bytes")
        ecs_resources = Resource.objects.filter(name__icontains="ecs")

        for resource in ecs_resources:
            logger.info("Starting ECS sync for resource %s (%s)", resource.pk, resource.name)
            try:
                manager = ECSResourceManager(resource)
            except Exception:
                logger.exception(
                    "Failed to initialize ECSResourceManager for resource %s (%s)",
                    resource.pk,
                    resource.name,
                )
                continue

            allocations = Allocation.objects.filter(
                status__name="Active",
                resources=resource,
            )
            synced_allocations = 0

            for allocation in allocations:
                namespace_name = manager.default_namespace_for_allocation(allocation)
                bucket_name = manager.default_bucket_for_allocation(allocation, namespace_name)

                try:
                    quota_tb = manager.get_namespace_quota_tb(namespace_name)
                except Exception:
                    logger.exception(
                        "Failed reading namespace quota for allocation %s namespace=%s",
                        allocation.pk,
                        namespace_name,
                    )
                    quota_tb = None

                if quota_tb is not None and quota_tb >= 0:
                    quota_bytes = int(quota_tb * BYTES_PER_TB)
                    self._upsert_allocation_attribute(allocation, quota_tb_type, quota_tb)
                    self._upsert_allocation_attribute(allocation, quota_bytes_type, quota_bytes)

                try:
                    usage = manager.collect_bucket_usage_data(namespace_name, bucket_name)
                    allocation.set_usage("Storage Quota (TB)", usage.total_size_tb)
                    allocation.set_usage("Quota_In_Bytes", usage.total_size_bytes)
                except Exception:
                    logger.exception(
                        "Failed reading bucket usage for allocation %s namespace=%s bucket=%s",
                        allocation.pk,
                        namespace_name,
                        bucket_name,
                    )
                    continue

                synced_allocations += 1

            try:
                summary = manager.update_resource_usage()
                logger.info(
                    "Updated resource %s allocated_tb=%s used_tb=%s",
                    resource.pk,
                    summary["allocated_tb"],
                    summary["used_tb"],
                )
            except Exception:
                logger.exception("Failed updating resource usage attributes for resource %s", resource.pk)

            logger.info(
                "Completed ECS sync for resource %s (%s). Synced allocations=%s",
                resource.pk,
                resource.name,
                synced_allocations,
            )

    def _upsert_allocation_attribute(self, allocation, attribute_type, value):
        attr, _ = AllocationAttribute.objects.get_or_create(
            allocation=allocation,
            allocation_attribute_type=attribute_type,
            defaults={"value": str(value)},
        )
        attr.value = str(value)
        attr.save()
