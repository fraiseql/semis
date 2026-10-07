---
title: Validating Seeds
description: Check a scenario against its schema with semis validate, and its prep-seed seeds and resolvers at confiture's five levels with semis validate-seeds
---

semis checks seeds at two moments. `semis validate` reads a scenario against the schema
and its pin without drawing a row. `semis validate-seeds` has
[confiture](https://fraiseql.dev/confiture/) judge a prep-seed scenario's seeds and the
project's resolvers together, at five levels. This page works in the project from
[Getting started](/getting-started/).

## The five levels

| Level | Reads | Checks |
|---|---|---|
| 1 | the seed files | each statement targets the staging schema; each `fk_*` column ends in `_id`; each UUID is one; each row is as wide as its column list |
| 2 | the schema | each staging table maps to a final table; foreign-key types; the trinity pattern; self-references |
| 3 | the resolvers | each `fn_resolve_*` function fills its final table, and joins each foreign key's UUID to its parent's `id` |
| 4 | a database | the connection, the tables, and the resolvers, dry-run inside a savepoint |
| 5 | a database | the seeds loaded and the resolvers run, inside a savepoint rolled back; a foreign key left `NULL` is a finding |

Levels 1 to 3 read files. Levels 4 and 5 run on a database and leave nothing behind.

## Write the resolvers

A project's resolvers promote each staged row into its final table, translating each
UUID into its parent's `pk_*`. They are the project's, not semis':

```sql title="db/schema/030_resolvers.sql"
CREATE FUNCTION catalog.fn_resolve_tb_continent() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO catalog.tb_continent (id, identifier, name)
    SELECT id, identifier, name FROM prep_seed.tb_continent;
END $$;

CREATE FUNCTION catalog.fn_resolve_tb_country() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO catalog.tb_country (id, identifier, fk_continent, name, iso_code)
    SELECT s.id, s.identifier, c.pk_continent, s.name, s.iso_code
    FROM prep_seed.tb_country AS s
    JOIN catalog.tb_continent AS c ON c.id = s.fk_continent_id;
END $$;
```

confiture reads the resolvers from the schema tree: `prep_seed: schema_dir:` in
`semis.yaml`, or the `ddl:` directory when the schema is read from one, as here.

## Validate a scenario's seeds

`semis validate-seeds` rehearses the scenario into a temporary directory and has confiture
judge exactly those files. Without a database URL it runs levels 1 to 3, and says so:

```bash
semis validate-seeds scenarios/continents.yaml
```

```text
no database URL: levels 1-3 only; levels 4-5 load the seeds and run the resolvers against a database
scenario continents is unpinned: its schema is not checked
validated 2 seed files at levels 1-3
no findings
```

With a database that holds the schema and the resolvers, it runs all five:

```bash
createdb continents_check
psql -d continents_check -f db/schema/010_catalog.sql -f db/schema/020_prep_seed.sql -f db/schema/030_resolvers.sql
semis validate-seeds scenarios/continents.yaml --database-url postgresql:///continents_check
```

```text
scenario continents is unpinned: its schema is not checked
validated 2 seed files at levels 1-5
no findings
```

The database is found as `semis apply` finds it: see
[the database URL](/reference/cli/#the-database-url). The whole validation runs in one
transaction, rolled back.

## Validate a directory of seeds

`--seeds` judges the files of a directory instead, such as seeds committed to the
repository. `--max-level` stops at a level:

```bash
semis seeds scenarios/continents.yaml -o db/seeds
semis validate-seeds --seeds db/seeds --max-level 3
```

```text
validated 2 seed files at levels 1-3
no findings
```

## Read the findings

A resolver that forgets a join leaves its foreign key unresolved:

```sql title="db/schema/030_resolvers.sql" {10-11}
CREATE FUNCTION catalog.fn_resolve_tb_continent() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO catalog.tb_continent (id, identifier, name)
    SELECT id, identifier, name FROM prep_seed.tb_continent;
END $$;

CREATE FUNCTION catalog.fn_resolve_tb_country() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO catalog.tb_country (id, identifier, fk_continent, name, iso_code)
    SELECT s.id, s.identifier, NULL, s.name, s.iso_code
    FROM prep_seed.tb_country AS s;
END $$;
```

Level 3 finds it without a database:

```bash
semis validate-seeds scenarios/continents.yaml --max-level 3
```

```text
scenario continents is unpinned: its schema is not checked
validated 2 seed files at levels 1-3
ERROR MISSING_FK_TRANSFORMATION schema/030_resolvers.sql:9
  fn_resolve_tb_country fills catalog.tb_country but never joins tb_continent on fk_continent_id: fk_continent is not resolved
  hint: Add: LEFT JOIN catalog.tb_continent ON tb_continent.id = fk_continent_id
1 finding: 1 ERROR
```

Findings are printed most severe first, each with its pattern, its file and line, and
confiture's hint. A `CRITICAL` or an `ERROR` exits 1; a `WARNING` or an `INFO` alone
exits 0.

A read-back scenario is refused: the five levels judge prep-seed seeds. A read-back run
is checked as it applies, and `semis apply --dry-run` applies it and rolls it back.

## Next steps

- [The two FK modes](/concepts/fk-modes/): the staging twins the levels judge
- [semis in CI](/guides/ci/): validation on every pull request
- [The semis command](/reference/cli/#semis-validate-seeds): every option of `validate-seeds`
- [confiture](https://fraiseql.dev/confiture/): the schema tool that judges the levels
