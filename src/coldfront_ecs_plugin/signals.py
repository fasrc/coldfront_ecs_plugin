import logging

from django.dispatch import receiver

from coldfront.core.allocation.signals import (
    allocation_autocreate,
    allocation_autoupdate,
)

from coldfront_ecs_plugin.utils import ECSResourceManager

logger = logging.getLogger(__name__)


@receiver(allocation_autocreate)
def ecs_allocation_autocreate(sender, **kwargs):
    """
    Automatically provision ECS objects for a new allocation.

    Behaviour for ECS:
    - Only runs for resources whose name indicates they are ECS-related.
    - Always creates:
        * a namespace named after the lab/allocation
        * a namespace quota (when allocation size is available)
        * a bucket within the namespace
    - Uses automation_specifications to configure bucket options where
      possible; otherwise raises a clear error if an option is not
      meaningful for ECS storage.
    - Raises an error if the namespace already exists.
    """
    approval_form_data = kwargs.get("approval_form_data") or {}
    allocation_obj = kwargs.get("allocation_obj")
    resource = kwargs.get("resource")

    if not allocation_obj or not resource:
        return

    # Only act on ECS resources
    if "ecs" not in resource.name.lower():
        return

    automation_specifications = approval_form_data.get("automation_specifications") or []
    project_title = allocation_obj.project.title

    try:
        manager = ECSResourceManager(resource)
        namespace_name = manager.default_namespace_for_allocation(allocation_obj)
        bucket_name = manager.default_bucket_for_allocation(allocation_obj, namespace_name)

        # Namespace must not already exist
        if manager.namespace_exists(namespace_name):
            raise ValueError(
                f"ECS namespace '{namespace_name}' already exists for resource '{resource.name}'."
            )

        resource_rg_name = manager.return_resource_replication_group()
        # Create namespace
        manager.create_namespace(
            namespace_name,
            replication_group=resource_rg_name,
            ldap_group=project_title
        )

        # Attach namespace quota based on allocation size (TB), when available
        quota_tb = None
        try:
            quota_tb = float(allocation_obj.size)
        except Exception:
            quota_tb = None

        if quota_tb is not None and quota_tb > 0:
            manager.assign_quota_to_namespace(namespace_name, quota_tb)

        # Map automation_specifications to ECS bucket options
        supported_bucket_opts = {"nfs_share"}
        unsupported = [
            opt for opt in automation_specifications if opt not in supported_bucket_opts
        ]
        if unsupported:
            raise ValueError(
                "ECS storage does not support the selected automation options: "
                + ", ".join(sorted(unsupported))
            )

        filesystem_enabled = "nfs_share" in automation_specifications

        # Create bucket with optional quota and filesystem access
        manager.create_bucket_for_namespace(
            namespace_name=namespace_name,
            bucket_name=bucket_name,
            block_limit_tb=quota_tb,
            filesystem_enabled=filesystem_enabled,
        )

        logger.info(
            "ECS provisioning completed for allocation %s on resource %s "
            "(namespace=%s, bucket=%s, automation=%s)",
            allocation_obj.pk,
            resource.name,
            namespace_name,
            bucket_name,
            sorted(automation_specifications),
            extra={"category": "integration:ecs", "status": "success"},
        )
        return "ecs"

    except Exception as exc:
        message = (
            f"ECS provisioning failed for allocation {getattr(allocation_obj, 'pk', '?')} "
            f"on resource {getattr(resource, 'name', '?')}: {exc}"
        )
        logger.exception(
            message,
            extra={"category": "integration:ecs", "status": "error"},
        )
        # Raising ValueError ensures the view can surface a clear error message
        raise ValueError(message) from exc


@receiver(allocation_autoupdate)
def ecs_allocation_autoupdate(sender, **kwargs):
    """
    Automatically update ECS namespace quota when an allocation's size changes.

    Behaviour:
    - Only runs for ECS resources.
    - Attempts to set the namespace quota to new_quota_value TB.
    - On error, logs and raises ValueError so the frontend can show the message.
    """
    allocation_obj = kwargs.get("allocation_obj")
    new_quota_value = kwargs.get("new_quota_value")

    if not allocation_obj or new_quota_value is None:
        return

    resource = allocation_obj.resources.first()
    if not resource or "ecs" not in resource.name.lower():
        return

    try:
        manager = ECSResourceManager(resource)
        namespace_name = manager.default_namespace_for_allocation(allocation_obj)
        manager.change_namespace_quota(namespace_name, float(new_quota_value))

        logger.info(
            "ECS namespace quota updated for allocation %s (resource=%s, namespace=%s, new_quota_tb=%s)",
            allocation_obj.pk,
            resource.name,
            namespace_name,
            new_quota_value,
            extra={"category": "integration:ecs", "status": "success"},
        )
        return "ecs"

    except Exception as exc:
        message = (
            f"ECS quota update failed for allocation {getattr(allocation_obj, 'pk', '?')} "
            f"on resource {getattr(resource, 'name', '?')}: {exc}"
        )
        logger.exception(
            message,
            extra={"category": "integration:ecs", "status": "error"},
        )
        raise ValueError(message) from exc

