from unittest.mock import MagicMock, patch

from django.test import TestCase

from coldfront.core.allocation.models import Allocation, AllocationStatusChoice
from coldfront.core.allocation.signals import (
    allocation_autocreate,
    allocation_autoupdate,
)
from coldfront.core.project.models import Project
from coldfront.core.resource.models import Resource, ResourceType


class ECSSignalTests(TestCase):
    def setUp(self):
        rtype = ResourceType.objects.create(
            name="Storage",
            description="ECS Storage",
        )
        self.resource = Resource.objects.create(
            resource_type=rtype,
            name="ecs-cluster-01",
            description="ECS test",
        )
        status = AllocationStatusChoice.objects.create(name="Active")
        project = Project.objects.create(
            title="ecs_test_project",
            description="",
            pi=None,
        )
        self.allocation = Allocation.objects.create(
            project=project,
            status=status,
            justification="ecs test allocation",
        )
        self.allocation.resources.add(self.resource)

        # Ensure signal handlers are imported
        import coldfront_ecs_plugin.signals  # noqa: F401

    @patch("coldfront_ecs_plugin.signals.ECSResourceManager")
    def test_allocation_autocreate_triggers_ecs_provisioning(self, mock_mgr_cls):
        manager = MagicMock()
        mock_mgr_cls.return_value = manager
        manager.default_namespace_for_allocation.return_value = "ns1"
        manager.default_bucket_for_allocation.return_value = "bucket1"
        manager.namespace_exists.return_value = False

        allocation_autocreate.send(
            sender=self.__class__,
            approval_form_data={"automation_specifications": ["nfs_share"]},
            allocation_obj=self.allocation,
            resource=self.resource,
        )

        manager.default_namespace_for_allocation.assert_called_once_with(self.allocation)
        manager.default_bucket_for_allocation.assert_called_once_with(self.allocation, "ns1")
        manager.create_namespace.assert_called_once_with("ns1")
        manager.assign_quota_to_namespace.assert_called_once()
        manager.create_bucket_for_namespace.assert_called_once()
        # nfs_share should enable filesystem access on the bucket
        _, kwargs = manager.create_bucket_for_namespace.call_args
        self.assertTrue(kwargs.get("filesystem_enabled", False))

    @patch("coldfront_ecs_plugin.signals.ECSResourceManager")
    def test_allocation_autocreate_raises_error_for_unsupported_automation(self, mock_mgr_cls):
        manager = MagicMock()
        mock_mgr_cls.return_value = manager
        manager.default_namespace_for_allocation.return_value = "ns1"
        manager.default_bucket_for_allocation.return_value = "bucket1"
        manager.namespace_exists.return_value = False

        with self.assertRaises(ValueError) as ctx:
            allocation_autocreate.send(
                sender=self.__class__,
                approval_form_data={"automation_specifications": ["snapshots", "cifs_share"]},
                allocation_obj=self.allocation,
                resource=self.resource,
            )

        self.assertIn("does not support the selected automation options", str(ctx.exception))

    @patch("coldfront_ecs_plugin.signals.ECSResourceManager")
    def test_allocation_autocreate_raises_error_if_namespace_exists(self, mock_mgr_cls):
        manager = MagicMock()
        mock_mgr_cls.return_value = manager
        manager.default_namespace_for_allocation.return_value = "ns1"
        manager.default_bucket_for_allocation.return_value = "bucket1"
        manager.namespace_exists.return_value = True

        with self.assertRaises(ValueError) as ctx:
            allocation_autocreate.send(
                sender=self.__class__,
                approval_form_data={"automation_specifications": []},
                allocation_obj=self.allocation,
                resource=self.resource,
            )

        self.assertIn("already exists", str(ctx.exception))

    @patch("coldfront_ecs_plugin.signals.ECSResourceManager")
    def test_allocation_autocreate_wraps_other_failures(self, mock_mgr_cls):
        mock_mgr_cls.side_effect = RuntimeError("connection failed")

        with self.assertRaises(ValueError) as ctx:
            allocation_autocreate.send(
                sender=self.__class__,
                approval_form_data={"automation_specifications": []},
                allocation_obj=self.allocation,
                resource=self.resource,
            )

        self.assertIn("ECS provisioning failed", str(ctx.exception))

    @patch("coldfront_ecs_plugin.signals.ECSResourceManager")
    def test_allocation_autoupdate_updates_namespace_quota(self, mock_mgr_cls):
        manager = MagicMock()
        mock_mgr_cls.return_value = manager
        manager.default_namespace_for_allocation.return_value = "ns1"

        allocation_autoupdate.send(
            sender=self.__class__,
            allocation_obj=self.allocation,
            new_quota_value=10.0,
        )

        manager.default_namespace_for_allocation.assert_called_once_with(self.allocation)
        manager.change_namespace_quota.assert_called_once_with("ns1", 10.0)

    @patch("coldfront_ecs_plugin.signals.ECSResourceManager")
    def test_allocation_autoupdate_raises_valueerror_on_failure(self, mock_mgr_cls):
        manager = MagicMock()
        mock_mgr_cls.return_value = manager
        manager.default_namespace_for_allocation.return_value = "ns1"
        manager.change_namespace_quota.side_effect = RuntimeError("quota API error")

        with self.assertRaises(ValueError) as ctx:
            allocation_autoupdate.send(
                sender=self.__class__,
                allocation_obj=self.allocation,
                new_quota_value=5.0,
            )

        self.assertIn("ECS quota update failed", str(ctx.exception))

