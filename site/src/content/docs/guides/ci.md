---
title: semis in CI
description: Check scenarios against their pins, committed seeds against a fresh run, and seeds against the resolvers, on every pull request
---

Three properties of semis make it useful in CI: a scenario refuses a schema that has
moved under its pin, prep-seed output is byte-reproducible, and confiture's five levels
judge seeds and resolvers together. A workflow checks all three on every change, to the
schema or to the scenarios.

## A workflow

This GitHub Actions workflow runs in the project from [Getting started](/getting-started/),
with the resolvers from [Validating seeds](/guides/validating-seeds/), the scenario
pinned, and its seeds committed under `db/seeds/`:

```yaml title=".github/workflows/seeds.yml"
name: Seeds

on:
  pull_request:
  push:
    branches: [main]

jobs:
  seeds:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:18
        env:
          POSTGRES_USER: seeds
          POSTGRES_PASSWORD: seeds
          POSTGRES_DB: continents_check
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
    env:
      CONFITURE_DATABASE_URL: postgresql://seeds:seeds@localhost:5432/continents_check
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.2.0
      - run: uv sync --locked
      - name: The committed pin is the schema's
        run: uv run semis pin scenarios/continents.yaml --check
      - name: Every scenario matches the schema it is pinned to
        run: uv run semis validate scenarios/continents.yaml
      - name: The committed seeds are what the scenario writes
        run: |
          uv run semis seeds scenarios/continents.yaml -o db/seeds
          git diff --exit-code -- 'db/seeds/*.sql'
      - name: The seeds and the resolvers pass confiture's five levels
        run: |
          psql "$CONFITURE_DATABASE_URL" -f db/schema/010_catalog.sql -f db/schema/020_prep_seed.sql -f db/schema/030_resolvers.sql
          uv run semis validate-seeds scenarios/continents.yaml
```

The actions are named by their release tags, for reading. semis' own workflows pin each
one to its commit, the release in a comment beside it, since a tag can be moved: do the
same in a workflow that holds a secret or an identity.

## What each step catches

- **`semis pin --check`** fails when the scenario's `continents.pin.json` is not the pin
  the schema gives it: the schema moved, or the scenario was never pinned. It names what
  moved and writes nothing; `semis pin` on a laptop accepts the change, as a commit to
  review.
- **`semis validate`** fails when the schema has moved under the scenario's pin, naming
  each table and column that moved, whatever the schema's source, and when the scenario no longer fits the schema: a table gone, a parent
  missing from the run, a staging twin missing. It draws no rows.
- **`semis seeds` and `git diff`** fail when the generated seed files differ from the
  committed ones. The comparison is byte for byte because prep-seed output is
  [byte-reproducible](/concepts/determinism/).
- **`semis validate-seeds`** fails on a `CRITICAL` or `ERROR` finding at any of
  [confiture's five levels](/guides/validating-seeds/#the-five-levels). Levels 4 and 5 run
  on the service database inside a transaction rolled back.

`CONFITURE_DATABASE_URL` gives `semis validate-seeds` its database. A command that writes
to a database does not take the ambient `DATABASE_URL` alone.

## Read-back scenarios in CI

A read-back scenario needs a database to generate, and its seed files carry PostgreSQL's
keys, so they are not committed. `semis apply --dry-run` runs the whole scenario, every
row checked and applied, and rolls it back:

```bash
uv run semis apply scenarios/countries.yaml --dry-run
```

It exits 1 on a refusal, and with confiture's own code on a confiture error:
[Exit codes and errors](/reference/exit-codes/).

## Next steps

- [Schema pins](/concepts/schema-pins/): re-pinning after a reviewed schema change
- [Validating seeds](/guides/validating-seeds/): reading the findings
- [Exit codes and errors](/reference/exit-codes/): what a failing step returns
