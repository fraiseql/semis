"""
fraiseql-semis: Semantic UUID-encoded fake data generator for PostgreSQL trinity pattern schemas.

This package provides tools for generating realistic, reproducible test and seed data
for PostgreSQL databases using the trinity pattern (UUID id, integer pk_*/fk_*, human-readable identifier).

Everything about the schema itself — what tables and columns exist, in what order rows may
be inserted, which columns a writer may supply, what each value must respect, and how a
seed file is written, applied and validated — comes from ``confiture.platform``. semis
supplies the values, the scenarios and the semantic UUID encoding.
"""

from importlib.metadata import version

from fraiseql_semis import emit, readback, seeds
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import (
    AlreadyAppliedError,
    CodeRegistryError,
    IncomparablePinError,
    PinError,
    ProjectError,
    ResetBlockedError,
    ResetScopeError,
    ResolutionError,
    RowContractError,
    ScenarioError,
    SchemaNotBuiltError,
    SemisError,
    UnreachableDatabaseError,
)
from fraiseql_semis.faker_provider import (
    TEXT_TYPES,
    CustomProviderRegistry,
    FakerProvider,
    Library,
    Provider,
    Rule,
)
from fraiseql_semis.generator import Copied, FakeDataGenerator
from fraiseql_semis.hierarchy import Hierarchy, Paths
from fraiseql_semis.pin import SchemaPin
from fraiseql_semis.project import Project, ProjectSchema
from fraiseql_semis.resolution import PrepSeedResolver, ReadBackResolver, Resolver
from fraiseql_semis.rows import Violation, check_row, require_row
from fraiseql_semis.scenario import (
    Deleted,
    ExistingTable,
    PinChange,
    Run,
    Scenario,
    ScenarioEntry,
    ScenarioManager,
    TableSpec,
    Validation,
    catalogue,
    init_scenario,
    shared_ids,
    single_table,
)
from fraiseql_semis.schema import ColumnFacts, SchemaFacts, TableFacts
from fraiseql_semis.uuid_generator import SemanticUUIDGenerator, UUIDFields

__version__ = version("fraiseql-semis")

__all__ = [
    "TEXT_TYPES",
    "AlreadyAppliedError",
    "CodeRegistryError",
    "ColumnFacts",
    "Copied",
    "CustomProviderRegistry",
    "Deleted",
    "ExistingTable",
    "FakeDataGenerator",
    "FakerProvider",
    "Hierarchy",
    "IncomparablePinError",
    "Library",
    "Paths",
    "PinChange",
    "PinError",
    "PrepSeedResolver",
    "Project",
    "ProjectError",
    "ProjectSchema",
    "Provider",
    "ReadBackResolver",
    "ResetBlockedError",
    "ResetScopeError",
    "ResolutionError",
    "Resolver",
    "RowContractError",
    "Rule",
    "Run",
    "Scenario",
    "ScenarioEntry",
    "ScenarioError",
    "ScenarioManager",
    "SchemaFacts",
    "SchemaNotBuiltError",
    "SchemaPin",
    "SemanticUUIDGenerator",
    "SemisError",
    "TableCodes",
    "TableFacts",
    "TableSpec",
    "UUIDFields",
    "UnreachableDatabaseError",
    "Validation",
    "Violation",
    "catalogue",
    "check_row",
    "emit",
    "init_scenario",
    "readback",
    "require_row",
    "seeds",
    "shared_ids",
    "single_table",
]
