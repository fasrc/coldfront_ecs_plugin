from unittest.mock import MagicMock, patch

from django.test import TestCase

from coldfront.core.resource.models import (
    AttributeType,
    Resource,
    ResourceAttribute,
    ResourceAttributeType,
    ResourceType,
)

from coldfront_ecs_plugin.utils import (
    BYTES_PER_TB,
    ECSResourceManager,
)


class ECSResourceManagerTests(TestCase):
    def setUp(self):
        rtype = ResourceType.objects.create(
            name="Storage",
            description="ECS Storage",
        )
        self.resource = Resource.objects.create(
            resource_type=rtype,
            name="ecs-cluster-01",
            description="ECS test cluster",
        )

        atype = AttributeType.objects.create(name="Text")
        url_type = ResourceAttributeType.objects.create(
            attribute_type=atype,
            name="url",
        )
        ResourceAttribute.objects.create(
            resource=self.resource,
            resource_attribute_type=url_type,
            value="https://ecs.example.local",
        )

    @patch("coldfront_ecs_plugin.utils.Client")
    def test_update_resource_usage_aggregates_namespace_and_bucket_data(self, mock_client_cls):
        """
        update_resource_usage should:
        - Sum namespace quotas into allocated_tb
        - Sum bucket usage into used_tb
        - Persist both on the backing Resource as resource attributes
        """
        fake_client = MagicMock()
        # One namespace with 1024 GB quota (1 TB)
        fake_client.namespace.list.return_value = {
            "namespace": [{"name": "ns1"}],
        }
        fake_client.namespace.get_namespace_quota.return_value = {
            "blockSize": 1024,
        }
        # One bucket in that namespace with 1 TB usage
        fake_client.bucket.list.return_value = {
            "object_bucket": [{"name": "b1"}],
        }
        # total_size is returned in KB by the billing helper in utils
        fake_client.billing.get_bucket_billing_info.return_value = {
            "total_size": (BYTES_PER_TB // 1024),
        }
        mock_client_cls.return_value = fake_client

        manager = ECSResourceManager(self.resource)
        summary = manager.update_resource_usage()

        self.assertAlmostEqual(summary["allocated_tb"], 1.0, places=5)
        self.assertAlmostEqual(summary["used_tb"], 1.0, places=5)

        # Values also stored as Resource attributes
        self.resource.refresh_from_db()
        self.assertAlmostEqual(self.resource.allocated_tb, 1.0, places=5)
        self.assertAlmostEqual(self.resource.used_tb, 1.0, places=5)

    @patch("coldfront_ecs_plugin.utils.Client")
    def test_collect_bucket_usage_data_handles_total_size_field(self, mock_client_cls):
        fake_client = MagicMock()
        fake_client.billing.get_bucket_billing_info.return_value = {
            "total_size": (2 * BYTES_PER_TB) // 1024,
        }
        mock_client_cls.return_value = fake_client

        manager = ECSResourceManager(self.resource)
        usage = manager.collect_bucket_usage_data("ns1", "bucket1")

        self.assertEqual(usage.bucket_name, "bucket1")
        self.assertEqual(usage.namespace, "ns1")
        self.assertAlmostEqual(usage.total_size_tb, 2.0, places=5)

    @patch("coldfront_ecs_plugin.utils.Client")
    def test_assign_quota_to_namespace_propagates_exceptions(self, mock_client_cls):
        fake_client = MagicMock()
        fake_client.namespace.update_namespace_quota.side_effect = RuntimeError("boom")
        mock_client_cls.return_value = fake_client

        manager = ECSResourceManager(self.resource)
        with self.assertRaises(RuntimeError):
            manager.assign_quota_to_namespace("ns1", 5.0)

