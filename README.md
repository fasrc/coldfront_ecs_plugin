# ColdFront ECS Plugin

Standalone ECS plugin package for ColdFront.

## Install

```bash
pip install coldfront-ecs-plugin
```

For local development from this directory:

```bash
pip install -e .
```

## Enable In ColdFront

Add `coldfront_ecs_plugin` to `INSTALLED_APPS` in your ColdFront settings.

Set ECS credentials and endpoint-related settings:

- `ECS_USER`
- `ECS_PASS`
- `ECS_CLIENT_VERSION` (optional, defaults to `3`)

## Model assumptions

- **ECS Resource URL**
  - Each ECS `Resource` in ColdFront must have a `ResourceAttribute` named **`url`** whose value is the ECS endpoint hostname, **without** port (for example: `https://ecs.example.org`).
  - The plugin uses this to build `token_endpoint` and `ecs_endpoint` as `"{url}:4443/login"` and `"{url}:4443"`.

- **Namespace mapping for Allocations**
  - The ECS namespace for an allocation is resolved as:
    - Allocation attribute `Namespace` (if present), otherwise
    - A slugified version of `allocation.project.title`.

- **Bucket mapping for Allocations**
  - The ECS bucket name for an allocation is resolved as:
    - Allocation attribute `Bucket` (if present), otherwise
    - A slugified `lab-<namespace>-bucket`.

- **Quota attributes on Allocations**
  - The sync and signals logic assume the standard storage attributes exist:
    - `AllocationAttributeType` named **`Storage Quota (TB)`**
    - `AllocationAttributeType` named **`Quota_In_Bytes`**
  - `ecs_sync` maintains both the **value** and **usage** for these attributes.

## Management command

After installation and app enablement:

```bash
python manage.py ecs_sync
```
