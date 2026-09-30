# Changelog

All notable changes to fraiseql-semis are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-09-30

The first release.

### Added

- **Semantic UUIDs**: each row's `id` encodes its table code, scenario id, version and
  sequence; `semis decode-uuid` reads one back, naming the table and the scenario.
- **Values from the schema's facts**, read through `confiture.platform`: type, declared
  length, enum labels, NOT NULL and UNIQUE; foreign keys from their `REFERENCES`.
- **Two declared FK modes**: prep-seed (a child carries its parent's UUID, written into
  the staging twin) and read-back (parents applied, their `pk_*` read back by UUID).
- **The row contract**: a row missing a NOT NULL column without a default is refused,
  with a per-column `trusts_trigger:` opt-out.
- **YAML scenarios** with overrides (a value, a list, a callable, or `null` for a
  nullable key), per-column providers, and hierarchies for self-referencing tables,
  including `ltree` paths in read-back.
- **Schema pins**: a digest of the facts semis consumes, refusing a replay against a
  schema that has moved and reporting what changed.
- **Provider libraries**: `i18n` and `organization` shipped; a project's own providers
  as `module:attribute`; installed libraries enabled by name through the
  `fraiseql_semis.providers` entry point.
- **The `semis` command**: `seeds`, `apply`, `generate`, `table`, `validate`,
  `validate-seeds` (confiture's five prep-seed levels), `list-scenarios`,
  `init-scenario` and `decode-uuid`, configured by `semis.yaml`.
- **Scale**: 21,500 rows per second generated; 100,000 rows written and applied in 15
  seconds, streamed rather than held.

### Security

- **A database URL's password is never printed.** semis masks it in whatever a refusal
  prints, and requires fraiseql-confiture 1.27, whose `CONFIG_003` refusal of a
  `--database-url` no longer repeats the password either.

[0.1.0]: https://github.com/fraiseql/semis/releases/tag/v0.1.0
