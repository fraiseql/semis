"""A trinity-pattern schema as DDL text: a schema without a project or a database.

Each catalog table a prep-seed test writes has its staging twin in ``prep_seed``, as a
project's resolvers read them: the same name, every foreign key a UUID named ``<fk>_id``, no
``REFERENCES``.
"""

TRINITY = """
CREATE SCHEMA catalog;

CREATE TABLE catalog.tb_continent (
    pk_continent BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name VARCHAR(50) NOT NULL
);

CREATE TABLE catalog.tb_country (
    pk_country BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_continent BIGINT NOT NULL REFERENCES catalog.tb_continent (pk_continent),
    iso_code CHAR(2) NOT NULL,
    full_label TEXT GENERATED ALWAYS AS (identifier || '-' || iso_code) STORED
);

CREATE SCHEMA prep_seed;

CREATE TABLE prep_seed.tb_continent (
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL,
    name VARCHAR(50)
);

CREATE TABLE prep_seed.tb_country (
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL,
    fk_continent_id UUID,
    iso_code CHAR(2)
);
"""

CODES = {
    "catalog.tb_continent": 0x02030405,
    "catalog.tb_country": 0x03040506,
}

# Every fact the row contract reads, on one table.
CONTRACT = """
CREATE SCHEMA catalog;

CREATE TYPE catalog.status AS ENUM ('draft', 'live', 'gone');

CREATE TABLE catalog.tb_product (
    pk_product BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name VARCHAR(50) NOT NULL,
    status catalog.status NOT NULL DEFAULT 'draft',
    kind catalog.status,
    code CHAR(2),
    sku TEXT UNIQUE,
    created_by UUID NOT NULL,
    note TEXT
);

CREATE SCHEMA prep_seed;

CREATE TABLE prep_seed.tb_product (
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL,
    name VARCHAR(50),
    status TEXT,
    kind TEXT,
    code CHAR(2),
    sku TEXT,
    created_by UUID,
    note TEXT
);
"""

CONTRACT_CODES = {"catalog.tb_product": 0x04050607}

# The schema scenarios/minimal_seed.yaml is written against: TRINITY, with a column a
# trigger fills.
WORKED = TRINITY.replace(
    "iso_code CHAR(2) NOT NULL,", "iso_code CHAR(2) NOT NULL,\n    created_by UUID NOT NULL,"
)

# A hierarchy as a trinity schema draws one: a nullable self-FK to the table's own pk_*, and a
# nullable ltree path its recalculation fills.
HIERARCHY = """
CREATE SCHEMA catalog;

CREATE TABLE catalog.tb_location (
    pk_location BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_parent_location BIGINT REFERENCES catalog.tb_location (pk_location),
    path LTREE,
    name VARCHAR(50) NOT NULL
);

CREATE SCHEMA prep_seed;

CREATE TABLE prep_seed.tb_location (
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL,
    fk_parent_location_id UUID,
    path LTREE,
    name VARCHAR(50)
);
"""

HIERARCHY_CODES = {"catalog.tb_location": 0x05060708}
