from django.apps import AppConfig


class ColdfrontEcsPluginConfig(AppConfig):
    name = "coldfront_ecs_plugin"
    verbose_name = "ColdFront ECS Plugin"

    def ready(self):
        """Register signal receivers so allocation_autocreate / allocation_autoupdate
        trigger ECS provisioning and quota updates.
        """
        import coldfront_ecs_plugin.signals  # noqa: F401
