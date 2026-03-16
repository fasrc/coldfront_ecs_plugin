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

## Management commands

After installation and app enablement:

- **`ecs_setup` – create ECS-specific attribute types**

  ```bash
  python manage.py ecs_setup
  ```

  - **What it does**
    - Creates or updates **ResourceAttributeTypes**:
      - `url` – hostname for the ECS endpoint (no port), used to build `https://<host>:4443`.
      - `replication_group` – human-readable replication group (vpool) **name** (for example `us1`), used to pick the vpool when creating namespaces/buckets.
    - Creates or updates **AllocationAttributeTypes**:
      - `Namespace` – logical namespace name to use for an allocation (optional override).
      - `Bucket` – logical bucket name to use for an allocation (optional override).
  - **What it does _not_ do**
    - Does **not** create generic ColdFront types such as `allocated_tb`, `used_tb`, `Storage Quota (TB)`, or `Quota_In_Bytes`. Those must come from ColdFront’s own management commands (`add_resource_defaults`, `add_allocation_defaults`) and are assumed to already exist.
    - Does **not** talk to ECS or create any namespaces/buckets; it only ensures database schema needed by the plugin is present.
  - **Org-specific assumptions**
    - Attribute names (`url`, `replication_group`, `Namespace`, `Bucket`) are hard-coded to match the organization’s ColdFront convention. If you rename them in your deployment, you must also adjust the plugin.

- **`ecs_sync` – pull ECS quota/usage into ColdFront**

  ```bash
  python manage.py ecs_sync
  ```

  - **Resource selection**
    - Looks for `Resource` rows whose **name contains `ecs`** (case-insensitive) and treats those as ECS resources. This is an **organization-specific heuristic**; if your ECS resources are named differently, you will need to adjust the filter in the command.
  - **Per-allocation sync**
    - For each active `Allocation` attached to an ECS resource (`status__name="Active"`):
      - Derives the **namespace** name using:
        - Allocation attribute `Namespace` (if present), otherwise
        - A slugified version of `allocation.project.title`.
      - Derives the **bucket** name using:
        - Allocation attribute `Bucket` (if present), otherwise
        - A slugified `lab-<namespace>-bucket`.
      - Reads the **namespace quota** from ECS (in GB) and converts it to:
        - `Storage Quota (TB)` (value) – stored on the allocation via an `AllocationAttribute`.
        - `Quota_In_Bytes` (value) – stored on the allocation via an `AllocationAttribute`.
      - Reads the **bucket usage** from ECS billing (size in KB) and updates:
        - `Storage Quota (TB)` (**usage**) – `allocation.set_usage("Storage Quota (TB)", …)`.
        - `Quota_In_Bytes` (**usage**) – `allocation.set_usage("Quota_In_Bytes", …)`.
    - Any failures when reading a particular namespace or bucket are logged and skipped; other allocations and resources continue to sync.
  - **Per-resource sync**
    - For each ECS resource, calls `ECSResourceManager.update_resource_usage()` which:
      - Calls the ECS capacity API to compute:
        - `capacity_tb` – total provisioned cluster capacity (GB → TB).
        - `used_tb` – `totalProvisioned_gb - totalFree_gb` (GB → TB).
      - Sums all **namespace quotas** on that ECS endpoint to compute:
        - `allocated_tb` – sum of per-namespace limits in TB (this can exceed `capacity_tb` if the cluster is overcommitted).
      - Writes these to the backing `Resource` attributes: `capacity_tb`, `used_tb`, `allocated_tb`.
  - **Org-specific assumptions**
    - Assumes **ColdFront’s default storage attributes** exist:
      - `AllocationAttributeType` named `Storage Quota (TB)`.
      - `AllocationAttributeType` named `Quota_In_Bytes`.
    - Uses **resource name contains `"ecs"`** as the way to identify ECS resources. This is specific to the reference deployment and may need to be changed in other environments.

## Signals

The plugin hooks into ColdFront’s allocation lifecycle via signals. Signal handlers live in `coldfront_ecs_plugin.signals` and are registered by the app config.

- **`allocation_autocreate` → `ecs_allocation_autocreate`**

  - **When it runs**
    - Triggered when a new allocation is auto-created in ColdFront.
    - Only acts if:
      - `allocation_obj` and `resource` are present in the signal kwargs, and
      - the resource’s `name` contains `"ecs"` (case-insensitive).
  - **What it does**
    - Instantiates `ECSResourceManager` for the target resource (using the resource’s `url` attribute and global ECS credentials).
    - Derives the namespace name from the allocation (see **Model assumptions** below).
    - Derives the bucket name from the allocation.
    - Checks whether the namespace already exists on ECS:
      - If it **does exist**, raises `ValueError` so the UI can show a clear error (prevents accidentally reusing another lab’s namespace).
    - Resolves the replication group (vpool) using:
      - The resource’s `replication_group` attribute (replication group name), if set, mapped to a vpool ID, or
      - The namespace’s `default_data_services_vpool` / the ECS defaults.
    - Creates the namespace on ECS, attaching:
      - The resolved replication group / vpool.
      - The project/lab name as the namespace’s LDAP group (in the reference organization’s setup).
    - If the allocation has a size:
      - Attaches a **namespace quota** equal to that size (in TB) via `update_namespace_quota`.
    - Interprets `automation_specifications` from the approval form:
      - `nfs_share` → supported; creates the bucket with `filesystem_enabled=True`.
      - `snapshots`, `cifs_share`, or any other value → **unsupported for ECS**; raises `ValueError` describing that these options are not valid for ECS.
    - Creates the bucket in that namespace, optionally with a bucket quota mirroring the allocation size.
  - **Error handling**
    - Any failure in the process is logged with `category="integration:ecs"`.
    - A `ValueError` is raised with a user-friendly message, which the ColdFront UI can display to the user.
  - **Org-specific assumptions**
    - Uses the project title and lab naming conventions to form namespace and bucket names (`lab-<namespace>-bucket`).
    - Assumes one LDAP group per project; passes the project title as the group when creating namespaces.
    - Treats `"ecs"` in the resource name as the indicator that a resource is backed by ECS.

- **`allocation_autoupdate` → `ecs_allocation_autoupdate`**

  - **When it runs**
    - Triggered when an allocation’s quota is changed via the ColdFront UI.
    - Only acts if:
      - `allocation_obj` and `new_quota_value` are present, and
      - the first resource on the allocation has `"ecs"` in its name.
  - **What it does**
    - Instantiates `ECSResourceManager` for the allocation’s ECS resource.
    - Derives the namespace name from the allocation.
    - Calls `change_namespace_quota(namespace_name, new_quota_tb)` to update the namespace’s quota on ECS.
  - **Error handling**
    - On failure, logs details and raises `ValueError("ECS quota update failed …")` so that the frontend can surface a clear error during the update workflow.
  - **Org-specific assumptions**
    - Same naming conventions and resource selection heuristic as `allocation_autocreate`.

## Model assumptions

- **ECS Resource URL**
  - Each ECS `Resource` in ColdFront must have a `ResourceAttribute` named **`url`** whose value is the ECS endpoint hostname, **without** port (for example: `https://ecs.example.org`).
  - The plugin uses this to build `token_endpoint` and `ecs_endpoint` as `"{url}:4443/login"` and `"{url}:4443"`.

- **Replication group (vpool) for bucket creation**
  - Creating a bucket requires a valid ECS replication group (vpool) ID. The plugin resolves it in this order:
    1. The namespace’s **default_data_services_vpool**, if the namespace exists and has one.
    2. Resource attribute **`replication_group`**, if set — the attribute value is the replication group **name** (e.g. `us1`), not the vpool URN; the plugin looks up the corresponding vpool ID from the cluster via `replication_group_id_from_name`.
  - If the namespace has no default vpool and no resource attribute is set, autocreate fails with a clear error. Run `ecs_setup` to ensure the **replication_group** attribute type exists, then set that attribute on the ECS resource to the replication group **name** (e.g. `us1`) to pin a specific vpool.

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
