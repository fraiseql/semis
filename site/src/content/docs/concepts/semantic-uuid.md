---
title: The Semantic UUID
description: How each row's id encodes its table code, scenario id, version and sequence, and how semis decode-uuid reads one back
---

Every `id` semis writes is a UUID that carries four numbers: the table's code, the
scenario's id, a version, and the row's sequence in its table. A row found in a dump, a
log or an API response says which table and which scenario it came from.

It is an [RFC 9562](https://www.rfc-editor.org/rfc/rfc9562) version 8 UUID, the version
the standard sets aside for a layout of one's own. The `8` that opens the third group is
its version, and the `8` that opens the fourth its variant, so a validator that accepts
any standard UUID accepts it, as Zod 4's `z.uuid()` and the npm `uuid` package's
`validate()` from 10.0 do. In PostgreSQL 17 and later, `uuid_extract_version(id) = 8`
picks out the rows semis wrote from those `gen_random_uuid()` filled.

```text
┌────────────┬──────────┬───┬──────────┬────┬──────────────────────┐
│ table code │ scenario │ 8 │ version  │ 10 │ sequence             │
│  32 bits   │ 16 bits  │ 4 │ 12 bits  │ 2  │ 62 bits              │
└────────────┴──────────┴───┴──────────┴────┴──────────────────────┘

02030405-5001-8001-8000-000000000042
^^^^^^^^ ^^^^ ^^^^ ^^^^^^^^^^^^^^^^
tb_continent  5001  v1  sequence 0x42 = 66
```

## Decode a UUID

`semis decode-uuid` prints the four fields. Run inside a project, it also names the table
the code belongs to, from `semis.yaml`, and the scenario the id belongs to, from the
scenario files. In the project from [Getting started](/getting-started/):

```bash
semis decode-uuid 02030405-5001-8001-8000-000000000004
```

```text
table_code   0x02030405   catalog.tb_continent
scenario_id  0x5001       continents
version      1
sequence     4
```

From Python, `SemanticUUIDGenerator.decode` returns the same four numbers:

```python
>>> from uuid import UUID
>>> from fraiseql_semis import SemanticUUIDGenerator
>>> SemanticUUIDGenerator(scenario_id=0x5001).decode(UUID("02030405-5001-8001-8000-000000000042"))
UUIDFields(table_code=33752069, scenario_id=20481, version=1, sequence=66)
```

## The four fields

| Field | Bits | Where it comes from |
|---|---|---|
| Table code | 32 | `table_codes:` in `semis.yaml`, keyed by the qualified table name |
| Scenario id | 16 | the scenario's `scenario_id:`, from `0x0` to `0xffff` |
| Version | 12 | `1` for every UUID semis writes |
| Sequence | 62 | the row's position in its table within the run, from 1 |

The same scenario run twice writes the same UUIDs: each is a function of the scenario and
the row's position, not of the run. [Determinism](/concepts/determinism/) describes what
follows from that.

## Codes are written in hex

The UUID's text is hexadecimal, so a code written in hex reads back as it was typed: the
scenario id `0x5001` appears as `5001` in the second group. Written in decimal, `5001`
encodes as `1389`, and the UUID no longer shows the number anyone typed. Table codes and
scenario ids are therefore written in hex, in `semis.yaml` and in scenario files alike.

A table code belongs to the qualified name, `catalog.tb_city`, so `catalog.tb_city` and
`etl.tb_city` are two tables with two codes. Two tables given one code are refused.

## The scenario files are the registry

A scenario's id is written in the scenario file and nowhere else. `semis decode-uuid`
names a UUID's scenario by reading the headers of the files under the project's
`scenarios:` directory, and `semis list-scenarios` lists them:

```bash
semis list-scenarios
```

```text
0x5001  prep-seed  continents  continents.yaml
0x5002  read-back  countries   countries.yaml
```

Two files that use one id are reported on stderr, and `semis list-scenarios` exits 1.
`semis init-scenario` writes a new scenario with the next free id.

## Next steps

- [The trinity pattern](/concepts/trinity-pattern/): the columns the UUID sits beside
- [semis.yaml](/reference/semis-yaml/): where table codes are declared
- [The scenario file](/reference/scenario-file/): `scenario_id:` and every other key
