"""Every confiture.platform name semis relies on is exported.

A call semis starts to need adds its name here, and only here.
"""

import confiture.platform

# docs/ARCHITECTURE.md §2: what crosses the boundary.
SEMIS_USES = (
    # calls
    "parse_schema",
    "introspect",
    "dependency_order",
    "writable_columns",
    "column_facts",
    "naming_hints",
    "write_copy_seed",
    "write_insert_seed",
    "apply_seeds",
    "validate_seeds",
    "diff",
    "tier_of",
    # types
    "Connection",
    "SchemaModel",
    "ObjectRef",
    "Column",
    "ColumnFacts",
    "ColumnReference",
    "TableHints",
    "SeedFile",
    "ApplyResult",
    "PrepSeedReport",
    "PrepSeedViolation",
    "SchemaDiff",
    "RiskTier",
    # errors
    "ConfiturError",
    "SchemaError",
    "SeedError",
    "ConfigurationError",
    "NotInModelError",
    "DependencyCycleError",
)


def test_every_name_semis_uses_is_exported() -> None:
    missing = [name for name in SEMIS_USES if not hasattr(confiture.platform, name)]
    assert missing == []
