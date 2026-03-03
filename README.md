# ColdFront ECS Plugin

Standalone ECS plugin package for ColdFront.

## Install

```bash
pip install coldfront-ecs-plugin
```

To install directly from a GitHub repository (replace `ORG`/`REPO` as appropriate):

```bash
pip install "git+https://github.com/ORG/REPO.git"
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

- **Allocation auto-create automation options**
  - When an allocation is auto-created for an ECS resource, the plugin will:
    - create a **namespace** named after the project (or `Namespace` allocation attribute, if set),
    - attach a **namespace quota** based on the allocation size (in TB), and
    - create a **bucket** within that namespace.
  - The `automation_specifications` selected on the allocation approval form are interpreted as:
    - `nfs_share`: supported for ECS; enables filesystem access on the bucket (`filesystem_enabled=True`).
    - `snapshots`, `cifs_share`, or any other value: **not supported** for ECS; the plugin will raise a clear error indicating these automation options are not valid for ECS storage.
  - A hard error is raised if a namespace with the computed name already exists on ECS for the target resource.

## Management commands

After installation and app enablement:

**Ensure plugin-specific database objects** (run once after installing the plugin):

```bash
python manage.py ecs_setup
```

This creates or updates only the attribute types specific to the ECS plugin: `url` (Resource), `Namespace` and `Bucket` (Allocation). It does not create types that ColdFront already provides via `add_resource_defaults` and `add_allocation_defaults` (e.g. `allocated_tb`, `used_tb`, `Storage Quota (TB)`, `Quota_In_Bytes`). Run ColdFront’s `add_resource_defaults` and `add_allocation_defaults` first if you have not already. Idempotent; safe to run multiple times.

**Sync ECS quota and usage into ColdFront:**

```bash
python manage.py ecs_sync
```
