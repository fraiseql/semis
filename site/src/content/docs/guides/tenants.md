---
title: Several Tenants
description: Write rows for several tenants from one scenario, each child in its parent's tenant, so an isolation test has another tenant's rows to hide
---

A test of tenant isolation proves something only when the other tenant's rows are there
to be hidden. A scenario that writes one tenant's rows lets every such test pass whether
the query is scoped or not. semis spreads a scenario's rows over several tenants from one
file, and keeps every foreign key inside its row's tenant.

## The project names its tenant column once

```sql title="db/schema/010_app.sql"
CREATE SCHEMA app;

CREATE TABLE app.tb_organization (
    pk_organization BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE app.tb_contact (
    pk_contact BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    tenant_id UUID NOT NULL,
    fk_organization BIGINT NOT NULL REFERENCES app.tb_organization (pk_organization),
    email TEXT NOT NULL UNIQUE
);

CREATE TABLE app.tb_order (
    pk_order BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_contact BIGINT NOT NULL REFERENCES app.tb_contact (pk_contact),
    total NUMERIC(10, 2) NOT NULL
);

CREATE TABLE app.tb_audit_log (
    pk_audit_log BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    tenant_id UUID,
    message TEXT NOT NULL
);
```

```yaml title="semis.yaml"
schema:
  ddl: db/schema/
table_codes:
  app.tb_organization: 0x01000001
  app.tb_contact: 0x01000002
  app.tb_order: 0x01000003
  app.tb_audit_log: 0x01000004
tenancy:
  column: tenant_id              # a table holding it is tenant-scoped
  table: app.tb_organization     # its rows are the tenants: tenant_id holds their id
  exclude: [app.tb_audit_log]    # holds the column, and belongs to no tenant
```

semis finds the tenant-scoped tables itself: a table holding `tenant_id` is one, unless
`exclude:` names it, and so is a table whose foreign key points at one, so an order,
which has no `tenant_id`, belongs to its contact's tenant. `include:` maps a table whose
tenant column has another name to that name. The rule is in the
[`semis.yaml` reference](/reference/semis-yaml/#tenancy).

## A scenario writes each tenant's rows

```yaml title="scenarios/two_tenants.yaml"
scenario_id: 0x5020
name: two_tenants
mode: read-back
seed: 42
tables:
  - name: app.tb_organization    # two organizations: two tenants
    count: 2
  - name: app.tb_contact         # five per tenant, each pointing at its own organization
    count: 10
  - name: app.tb_order           # no tenant_id: an order is its contact's tenant's
    count: 40
```

The rows the run writes into the tenant table are its tenants. A scoped row takes them
round-robin, and is written its tenant's value in `tenant_id`; each of its foreign keys
into a scoped table points at a row of that tenant, round-robin among them. A tree
stays in its root's tenant, and a translation row in its entity's. The run says what it
spread:

```text
scenario two_tenants spreads app.tb_contact, app.tb_order over 2 tenants of app.tb_organization
```

Applied, no key crosses a tenant:

```sql
SELECT count(*) AS crossing
FROM app.tb_order AS o
JOIN app.tb_contact AS c ON c.pk_contact = o.fk_contact
JOIN app.tb_organization AS t ON t.pk_organization = c.fk_organization
WHERE c.tenant_id <> t.id;
-- crossing 0
```

The UUIDs are those the scenario writes without tenants; the tenant column and the keys
are what move.

## What is refused

A tenant in which a child has rows and its parent none is refused before a row is
drawn, naming the key and the tenant: give the parent a row in every tenant, or say the
key crosses. The tenant column is the run's to write: a scenario that overrides, copies,
fills, trusts or gives a provider to it is refused. A key into a table listed under
`existing:` that holds the tenant column is refused too, since semis does not read the
tenants of rows it did not write.

## Rows that cross on purpose

A query that must refuse another tenant's rows is tested on rows pointing across.
`crosses_tenants:` names the keys that point at a parent of any tenant, round-robin, as
without tenants:

```yaml
  - name: app.tb_order
    count: 40
    crosses_tenants: [fk_contact]   # orders of any tenant's contacts
```

## Tenants the run does not write

When the tenants already exist, list them under `existing:`, by name, in the tenant
table: they are the run's tenants, in that order. A whole-table read is refused, so a
run's tenants never depend on what a database holds.

```yaml
existing:
  - name: app.tb_organization
    where: {identifier: [acme, globex]}
```

A project with no tenant table lists its tenants' values in the scenario instead:

```yaml
tenants: [00000000-0000-4000-8000-00000000000a, 00000000-0000-4000-8000-00000000000b]
```

## Every view sees more than one tenant

Once a scenario is applied, [`semis coverage`](/reference/cli/#semis-coverage) says what
each view, materialized view and TVIEW reading its tables holds: its rows, and, for a view
that outputs the tenant column, how many tenants. A view holding one tenant's rows cannot
show that another's are hidden; in CI, `--fail-on-single-tenant` makes that a failure:

```bash
semis coverage scenarios/two_tenants.yaml --fail-on-single-tenant
```

## Views scoped by the request's identity

A query view may scope its rows by who asks: fraiseql sets session variables per
request, from the identity it serves, and the view reads them, often through one filter
function, `current_setting('app.agent_role', true)`. With nothing set, such a view shows
no rows, which `semis coverage` would report as `no rows` over full tables. Name, in
`semis.yaml`, the identity the report reads as:

```yaml
coverage:
  session_variables:
    app.agent_role: manager
```

Each is set with `set_config` in the report's transaction, which is rolled back. Only
custom settings, `prefix.name`, are taken; a view that stays empty with them set is a
gap in the seeds. A row-level security policy reads them too, but applies only to a role
it binds: a superuser, or a table's owner while the table does not force its policies,
reads every row whatever is set.

## Next steps

- [The scenario file](/reference/scenario-file/): `tenants:` and `crosses_tenants:`
- [`semis.yaml`](/reference/semis-yaml/#tenancy): the tenancy rule
- [The two FK modes](/concepts/fk-modes/): the same spread in prep-seed and read-back
