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

# A child copying its parent's id, as a tenant column does, with a column for every case
# copies: refuses: a nullable key, a self-FK, a parent the run may not write, and parent
# columns left NULL, defaulted, trusted to a trigger or set as a hierarchy's path.
COPIES = """
CREATE SCHEMA tenant;

CREATE TABLE tenant.tb_category (
    pk_category BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE
);

CREATE TABLE tenant.tb_organization (
    pk_organization BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name VARCHAR(50) NOT NULL,
    note TEXT,
    status TEXT DEFAULT 'live',
    created_by UUID,
    fk_parent_organization BIGINT REFERENCES tenant.tb_organization (pk_organization),
    path LTREE
);

CREATE TABLE tenant.tb_contact (
    pk_contact BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    tenant_id UUID NOT NULL,
    backup_tenant_id UUID,
    label TEXT,
    fk_customer_org BIGINT NOT NULL REFERENCES tenant.tb_organization (pk_organization),
    fk_backup_org BIGINT REFERENCES tenant.tb_organization (pk_organization),
    fk_referrer BIGINT REFERENCES tenant.tb_contact (pk_contact),
    fk_category BIGINT REFERENCES tenant.tb_category (pk_category)
);

CREATE SCHEMA prep_seed;

CREATE TABLE prep_seed.tb_organization (
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL,
    name VARCHAR(50),
    note TEXT,
    status TEXT,
    created_by UUID,
    fk_parent_organization_id UUID,
    path LTREE
);

CREATE TABLE prep_seed.tb_contact (
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL,
    tenant_id UUID,
    backup_tenant_id UUID,
    label TEXT,
    fk_customer_org_id UUID,
    fk_backup_org_id UUID,
    fk_referrer_id UUID,
    fk_category_id UUID
);
"""

COPIES_CODES = {
    "tenant.tb_category": 0x06070809,
    "tenant.tb_organization": 0x0708090A,
    "tenant.tb_contact": 0x08090A0B,
}
