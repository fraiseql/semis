# fraiseql-semis

**Reproducible seed and test data for PostgreSQL trinity-pattern schemas.**

semis generates rows: which rows exist, what each value looks like, and what UUID
identifies each one. It decides nothing about the schema those rows land in — every
question of the form *"what does the schema say?"* goes to
[confiture](https://github.com/fraiseql/confiture), which reads the DDL or the live
database, orders tables by their real foreign keys, says which columns a writer may supply
and what each value must respect, and writes, applies and validates the seed files.

---

## What it does

- 🎯 **Semantic UUIDs** — every `id` encodes its table, scenario, version and sequence, so
  a row in a dump tells you where it came from; the `identifier` slug carries the scenario
  too, so two scenarios can share a database
- 🎲 **Faker, driven by facts** — a value satisfies the column that receives it: its type,
  its enum values, its declared length, its NOT NULL
- 🔗 **Foreign keys that are real** — the parent table and column come from the schema's
  actual foreign key, never from the spelling of `fk_continent`
- 📋 **YAML scenarios** — a run is a reviewable file, pinned to the schema it was written
  against
- 🔁 **Reproducible** — same scenario, same seed, same schema, same bytes
- 🚫 **No invented keys** — PostgreSQL fills `pk_*`; semis never writes one

## What it does not do

semis reads no `information_schema`, sorts no tables, and writes no seed SQL itself. All
three are confiture's, and going through it is why `VARCHAR(50)` gets fifty characters and
why a `REFERENCES` clause is read rather than guessed. Its only SQL is the handful of
parameterised statements in one module, for read-back, the re-apply check, the reset and
their lock.

---

## Install

```bash
uv add fraiseql-semis          # brings fraiseql-confiture>=1.30,<2
```

semis runs on Python 3.14 and writes to PostgreSQL 16 or later.

## The chain, end to end

This is the whole idea in one screen — generate, write, apply, read back:

```python
from pathlib import Path

from fraiseql_semis import FakeDataGenerator, SchemaFacts, TableCodes, seeds

database_url = "postgresql:///myproject_dev"
codes = TableCodes({"catalog.tb_continent": 0x02030405})

# the project's DDL tree; SchemaFacts.from_env("development", table_codes=codes) reads
# a confiture environment's build, and .from_database(url, …) a live database
facts = SchemaFacts.from_source(Path("db/schema"), table_codes=codes)
gen   = FakeDataGenerator(facts, scenario_id=0x5001, seed=42)

rows = gen.generate_rows(
    "catalog.tb_continent",
    count=3,
    overrides={"identifier": lambda i: f"cont-{i + 1}", "name": lambda i: f"Continent {i + 1}"},
)
seed = seeds.write(
    "db/seeds/010_tb_continent.sql", "catalog.tb_continent", rows, facts=facts, mode="read-back"
)
seeds.apply(database_url, [seed.path])
```

What arrives in PostgreSQL — `psql` output from exactly this chain. The two key columns
are the point:

```
 pk_continent |                  id                  | identifier |    name
--------------+--------------------------------------+------------+-------------
            1 | 02030405-5001-8001-8000-000000000001 | cont-1     | Continent 1
            2 | 02030405-5001-8001-8000-000000000002 | cont-2     | Continent 2
            3 | 02030405-5001-8001-8000-000000000003 | cont-3     | Continent 3
(3 rows)
```

`pk_continent` is PostgreSQL's — semis could not write it if it wanted to:

```python
>>> write_copy_seed(path, "catalog.tb_continent", ["pk_continent", "id"], rows, model=model)
SeedError: PostgreSQL fills catalog.tb_continent.pk_continent (an identity, generated or
           serial column): leave it out of the seed
```

`id` is semis' — and it decodes:

```bash
$ semis decode-uuid 02030405-5001-8001-8000-000000000001
table_code   0x02030405   catalog.tb_continent
scenario_id  0x5001       minimal_seed
version      1
sequence     1
```

---

## The two FK modes

A child needs its parent's integer `pk_*`, and semis cannot choose one. **A scenario
declares which of the two ways it closes that gap**; semis refuses to guess, because a
scenario that picks its mode by probing the database means two different things in CI and
on a laptop.

| | **prep-seed** | **read-back** |
|---|---|---|
| Rows are written into | the staging twin, `prep_seed.<table>` | the table itself |
| A child's FK carries | the parent's **UUID**, as `<fk>_id` | the parent's **integer**, learned after apply |
| Database needed to generate | no | yes |
| Translation done by | the project's `fn_resolve_*` functions | a join on the UUID semis wrote |
| Output is | byte-reproducible | reproducible except the FK integers, which are PostgreSQL's |
| Default writer | `INSERT`; confiture's prep-seed level 1 reads `COPY` too | `COPY` |

A prep-seed scenario names the catalog tables, whose foreign keys, constraints and pin
semis reads, and writes each into its **staging twin**: the same table name in the
project's staging schema, every foreign key a UUID named `<fk>_id` — the layout
confiture's prep-seed validation checks and a project's `fn_resolve_*` functions read.
A table without its twin is refused before a row is drawn.

```yaml
# scenarios/minimal_seed.yaml
scenario_id: 0x5001
name: minimal_seed
description: Seven continents and the countries on them
mode: prep-seed          # required: prep-seed | read-back
locale: en_US
seed: 42

tables:
  - name: catalog.tb_continent
    count: 7
    overrides:
      name: [Africa, Antarctica, Asia, Europe, North America, Oceania, South America]
  - name: catalog.tb_country
    count: 50
    trusts_trigger: [created_by]      # a trigger fills it; do not refuse the row
```

A NOT NULL foreign key's parent is generated in the same run — a missing one is refused
before a row is drawn. A nullable key whose parent the run lacks is written `NULL`, and
the run says so; one overridden `null` is written `NULL` even when its parent is there:

```yaml
  - name: inventory.tb_item
    count: 50
    overrides: {fk_account: null, fk_order: null}   # no account, no order
```

In read-back, a parent may also be rows the database already holds — reference rows the
schema's own DDL inserts. The scenario lists their table under `existing:`, and children
point at them, round-robin, as at a parent the run generated:

```yaml
existing:
  - name: shop.tb_category            # every row, round-robin, in pk_* order
  - name: shop.tb_country
    where: {identifier: [fr, de, es]} # these rows, in this order
```

A nullable column that is not a key — a soft-delete or an audit column — is written
`NULL` unless the scenario names it: by an override, by a provider for it, or under
`fill:`, so a scenario with no overrides writes rows every view sees. The run names, per
table, the columns it left `NULL`; `fill: all` draws every one.

```yaml
  - name: shop.tb_customer
    count: 3
    fill: [created_by]                # drawn; deleted_at is written NULL
```

A column may copy a value of the row its foreign key points at — a tenant id carried
down from the organization a contact belongs to — so no UUID is computed by hand:

```yaml
  - name: tenant.tb_organization
    count: 6
  - name: tenant.tb_contact
    count: 12
    copies:
      tenant_id: fk_customer_org.id   # the id of the organization fk_customer_org points at
```

From Python, a scenario runs through `ScenarioManager`, which writes one seed file per
table. `semis pin` pins it: it writes `minimal_seed.pin.json` beside the scenario, the
digest of the facts semis read and the facts themselves, so a replay against a moved
schema is refused, naming the table and the column that moved.

```python
from fraiseql_semis import ScenarioManager

manager = ScenarioManager(facts)            # the SchemaFacts above
run = manager.execute(manager.load("scenarios/minimal_seed.yaml"), Path("db/seeds/prep"))
print(*run.notices)    # scenario minimal_seed is unpinned: its schema is not checked
```

From the command line, a project names its schema and its table codes once, in
`semis.yaml`; a scenario names neither:

```yaml
# semis.yaml
schema:
  env: development          # a confiture environment; or  ddl: db/schema/  or
                            #   database: {schemas: [catalog]}
scenarios: scenarios/
table_codes:                # or a path to a YAML file of them
  catalog.tb_continent: 0x02030405
  catalog.tb_country: 0x03040506
providers: [i18n, organization, myproject.fake:PROVIDERS]   # optional; see Providers
prep_seed:
  prep_seed_schema: prep_seed   # where prep-seed scenarios write; the default
  schema_dir: db/0_schema       # the tree holding the resolvers; default: a ddl: directory
  catalog_schema: catalog       # optional: a final table's fallback schema
```

```bash
semis seeds scenarios/minimal_seed.yaml -o db/seeds/prep   # prep-seed: writes files, no database
semis seeds scenarios/minimal_seed.yaml --dry-run          # every row checked, nothing written
semis apply scenarios/minimal_seed.yaml -o db/seeds/run    # writes and applies, one transaction
semis apply scenarios/minimal_seed.yaml                    # applies, and keeps no file
semis reset scenarios/minimal_seed.yaml                    # deletes the scenario's rows, only those
semis generate scenarios/minimal_seed.yaml -o db/seeds/run # seeds for prep-seed, apply for read-back
semis validate scenarios/minimal_seed.yaml                 # schema and pin checked, no rows drawn
semis pin scenarios/minimal_seed.yaml                      # writes minimal_seed.pin.json beside it
semis pin scenarios/minimal_seed.yaml --check              # exits 1 if the pin would change
semis validate-seeds scenarios/minimal_seed.yaml           # confiture's five prep-seed levels
semis validate-seeds --seeds db/seeds/prep --max-level 3   # a directory's seeds, files only
semis table catalog.tb_continent --count 3 --mode prep-seed --scenario-id 0x5001 -o out
semis list-scenarios
semis init-scenario demo --mode read-back                  # a template, with the next free id
```

`apply` connects where confiture would: `--database-url`; else, for a project read from an
`env:`, that environment's `database_url`, with a `CONFITURE_DATABASE_URL` beside it
refused as ambiguous; else `CONFITURE_DATABASE_URL`. The ambient `DATABASE_URL` alone is
refused to a command that writes. The whole run is one transaction, committed at the
end, and `apply --dry-run` runs it and rolls it back. A refusal exits 1, naming the table
and the column; a confiture error exits with confiture's own code and hint.

`validate-seeds` rehearses a prep-seed scenario into a temporary directory and has
confiture judge exactly its files; `--seeds DIR` judges a directory instead. Levels 1–3
read files. Levels 4–5 load the seeds into the staging twins and run the project's
resolvers on the database, found as `apply` finds it, inside a savepoint rolled back:
nothing is left behind. With no database URL it runs levels 1–3 and says so. Findings are
printed most severe first, each with its file and line and confiture's hint, and a
`CRITICAL` or `ERROR` exits 1.

`semis.yaml` loads into `fraiseql_semis.Project`, which a tool that drives semis from
Python builds directly, with no file.

### Providers

semis ships two provider libraries — `i18n` (ISO country, language and currency codes,
locales, time zones) and `organization` (company names, SIREN/SIRET and French VAT
numbers that check, job titles, contacts). A library named under
`providers:` draws every column its rules match by name and type that the run fills —
`lang VARCHAR(2)` receives `fr`, `currency CHAR(3)` an ISO 4217 code — and never a value
longer than the column holds. A rule does not make a nullable column drawn; `fill:` does. A scenario names one provider for one column:

```yaml
  - name: catalog.tb_currency
    count: 20
    providers: {iso_code: i18n.currency_code, name: slogan}
```

`slogan` is the project's own: `myproject.fake:PROVIDERS` is a mapping of names to
functions taking the run's seeded `Faker` and the column's facts. It is imported, so it
is installed or on `PYTHONPATH`; no scenario string is ever evaluated.

### Your own provider library

A library another project can install — private to a company, or published — is a
`Library` registered under the `fraiseql_semis.providers` entry point:

```python
# acme_semis/__init__.py
from faker import Faker

from fraiseql_semis import TEXT_TYPES, ColumnFacts, Library, Rule


def ticker(faker: Faker, _column: ColumnFacts) -> str:
    return faker.lexify("????").upper()


LIBRARY = Library(
    "acme",                                   # the name semis.yaml enables it by
    {"ticker": ticker},                       # a scenario names acme.ticker
    rules=(Rule("ticker", names=frozenset({"ticker", "symbol"}), types=TEXT_TYPES),),
)
```

```toml
# acme-semis's pyproject.toml
[project.entry-points."fraiseql_semis.providers"]
acme = "acme_semis:LIBRARY"
```

Installed beside semis (`uv add acme-semis`), it is enabled as a shipped one is —
`providers: [i18n, acme]` — and only then: installing a library enables nothing, and
only the libraries `semis.yaml` names are imported. A name two installed packages
claim, or a shipped name, is refused, naming the packages.

### Hierarchies

A table whose foreign key points at itself — a location inside a location — declares its
tree, and semis draws it breadth-first: the roots carry `NULL`, and every later row hangs
from an earlier one, `fan_out` children to a parent.

```yaml
  - name: catalog.tb_location
    count: 20                        # levels of 2, 6 and 12 rows
    hierarchy:
      parent: fk_parent_location     # the nullable self-FK that builds the tree
      roots: 2
      fan_out: 3
      path: path                     # read-back only: an ltree of pk_* labels, 1.5.12
```

In prep-seed a child carries its parent's UUID, like any child. In read-back each level is
written, applied and read back before the next is drawn, one seed file per level, and
`path:` is set from the keys PostgreSQL gave: the parent's path, a dot, the row's own
`pk_*`.

A flat set of rows, every one a root, needs no tree: override the nullable self-FK `null`.

```yaml
  - name: catalog.tb_location        # a flat set: no row has a parent
    count: 5
    overrides: {fk_parent_location: null}
```

A self-referencing table that neither declares a `hierarchy:` nor leaves its key `null` is
refused.

---

## Running it again

A scenario applies once, to a reset database. A row's UUID is a function of its scenario
and its position, so a second run would write the same ones. semis asks first, one query
per table on the scenario's UUID range, and refuses before anything is written. The check
skips a table with no `id` column, or whose `id` is not a uuid, since it has no range to
ask:

```bash
semis apply scenarios/minimal_seed.yaml -o db/seeds/again --database-url postgresql:///myproject_dev
```

```text
scenario minimal_seed is already applied: prep_seed.tb_continent holds its rows
Hint: A scenario applies once, to a reset database. Apply it with semis apply --reset, or run semis reset first: either deletes the scenario's rows, and only those.
```

`--reset` deletes the rows the scenario wrote, then applies it, in one transaction: a
failed apply leaves the first run's rows in place. `semis reset` deletes them alone.

```bash
semis apply scenarios/minimal_seed.yaml --reset -o db/seeds/again --database-url postgresql:///myproject_dev
```

Either deletes, from each table the run writes into, the rows in the scenario's UUID
range, children first: rows another tool or scenario wrote, and the reference rows the
schema's DDL inserted, stay. A table left empty has its identity restarted, so a table the
scenario owns alone applies again exactly as it first did, keys included. A row the
scenario did not write that points at one it did, from any schema, refuses the reset
before anything is deleted, whatever the key's `ON DELETE`: semis never cascades into a
row it did not write.

There is no upsert. `ON CONFLICT DO NOTHING` would apply a scenario that has drifted
from the rows it once wrote and say nothing; a re-run that starts from a reset database
writes exactly what the scenario says.

## Two things semis insists on

**A row is complete before it is written.** Confiture's writers do not check NOT NULL —
deliberately, because a trigger may fill a column and the schema model cannot know. So
semis checks: a column that is NOT NULL with no default must be in the row with a real
value, or the row is refused, naming the table, the column and the fact it failed. A
column a trigger really does fill is named in `trusts_trigger:`, per table, per column.
Without this the gap surfaces at apply time as a constraint violation against a file, with
no line and no generator in sight.

**A scenario knows the schema it was written against.** Each one records a digest of the
facts semis consumes — writable columns, their types, nullability, defaults, enums, checks,
FK targets — and keeps those facts beside it, so a replay against a moved schema is
refused naming each table and column that moved, whatever the schema's source. Adding an index does not move the digest; adding `NOT NULL` to a
column semis writes does. `--no-pin` skips the check for one run and says so.

---

## Documentation

| | |
|---|---|
| [docs/PRD.md](./docs/PRD.md) | What semis is for, and for whom |
| [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) | The boundaries, the two modes, determinism, the pin, the decision log |

## Development

```bash
uv sync --all-extras
uv run pytest tests/unit/          # no database needed
uv run ruff check . && uv run ruff format --check .
uv run ty check src/fraiseql_semis/

export SEMIS_TEST_DATABASE_URL=postgresql:///semis_test
uv run pytest tests/integration/
```

Unit tests take their schema from a DDL string through `parse_schema`, so the generator,
the row contract and the UUID encoding are all testable without PostgreSQL. Tests under
`tests/contract/` pin the confiture behaviours semis relies on, so an upstream change
fails here with a name.

## License

MIT — see [LICENSE](./LICENSE).
