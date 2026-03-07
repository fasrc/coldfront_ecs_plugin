"""
Create or ensure only the database objects specific to the ECS plugin.

Assumes ColdFront's add_resource_defaults and add_allocation_defaults have
already been run (so AttributeTypes and types like allocated_tb, used_tb,
Storage Quota (TB), Quota_In_Bytes already exist). This command only adds
plugin-specific attribute types: url, replication_group (Resource), Namespace and Bucket (Allocation).
Idempotent; safe to run multiple times.
"""
from django.core.management.base import BaseCommand

from coldfront.core.allocation.models import (
    AttributeType as AllocationAttributeTypeModel,
    AllocationAttributeType,
)
from coldfront.core.resource.models import (
    AttributeType as ResourceAttributeTypeModel,
    ResourceAttributeType,
)


class Command(BaseCommand):
    help = (
        "Ensure ECS-plugin-specific attribute types exist (url, Namespace, Bucket). "
        "Requires add_resource_defaults and add_allocation_defaults to have been run. "
        "Idempotent; safe to run multiple times."
    )

    def handle(self, *args, **options):
        self.stdout.write("Ensuring ECS plugin attribute types...")

        # Plugin-only ResourceAttributeTypes: url, replication_group (Text from add_resource_defaults)
        text_res = ResourceAttributeTypeModel.objects.get(name="Text")
        for name in ("url", "replication_group"):
            _, created = ResourceAttributeType.objects.update_or_create(
                name=name,
                defaults={"attribute_type": text_res},
            )
            self.stdout.write("  ResourceAttributeType: " + name + (" (created)" if created else " (exists)"))

        # Plugin-only AllocationAttributeTypes: Namespace, Bucket (Text from add_allocation_defaults)
        text_alloc = AllocationAttributeTypeModel.objects.get(name="Text")
        for name in ("Namespace", "Bucket"):
            _, created = AllocationAttributeType.objects.update_or_create(
                name=name,
                defaults={
                    "attribute_type": text_alloc,
                    "has_usage": False,
                    "is_private": False,
                    "is_changeable": False,
                },
            )
            self.stdout.write(f"  AllocationAttributeType: {name}" + (" (created)" if created else " (exists)"))

        self.stdout.write(self.style.SUCCESS("ECS plugin setup complete."))
