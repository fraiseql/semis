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

# A translated entity: categories, their labels per locale in tl_category, and the locale
# table the schema's DDL fills, read by its code.
TRANSLATED = """
CREATE SCHEMA catalog;

CREATE TABLE catalog.tb_locale (
    pk_locale BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    code TEXT NOT NULL UNIQUE
);

CREATE TABLE catalog.tb_category (
    pk_category BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name VARCHAR(50) NOT NULL
);

CREATE TABLE catalog.tl_category (
    fk_category BIGINT NOT NULL REFERENCES catalog.tb_category (pk_category),
    fk_locale BIGINT NOT NULL REFERENCES catalog.tb_locale (pk_locale),
    label VARCHAR(80) NOT NULL,
    UNIQUE (fk_category, fk_locale)
);

CREATE SCHEMA prep_seed;

CREATE TABLE prep_seed.tb_category (
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL,
    name VARCHAR(50)
);

CREATE TABLE prep_seed.tl_category (
    fk_category_id UUID,
    fk_locale_id UUID,
    label VARCHAR(80)
);
"""

TRANSLATED_CODES = {"catalog.tb_category": 0x0D0E0F10, "catalog.tl_category": 0x0E0F1011}


# Tenants: organizations are the tenants; contacts and projects hold a tenant_id; a task
# belongs to its project's tenant through its key alone; categories are shared; folders
# are a tree within a tenant; the audit log holds a tenant_id and is not tenant-scoped.
TENANTS = """
CREATE SCHEMA app;

CREATE TABLE app.tb_organization (
    pk_organization BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name VARCHAR(50) NOT NULL,
    fk_parent_organization BIGINT REFERENCES app.tb_organization (pk_organization)
);

CREATE TABLE app.tb_category (
    pk_category BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE app.tb_contact (
    pk_contact BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    fk_organization BIGINT NOT NULL REFERENCES app.tb_organization (pk_organization),
    fk_category BIGINT NOT NULL REFERENCES app.tb_category (pk_category),
    email TEXT NOT NULL UNIQUE,
    tenant_label UUID
);

CREATE TABLE app.tb_project (
    pk_project BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    tenant_id UUID NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE app.tb_task (
    pk_task BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_project BIGINT NOT NULL REFERENCES app.tb_project (pk_project),
    fk_assignee BIGINT REFERENCES app.tb_contact (pk_contact),
    title TEXT NOT NULL
);

CREATE TABLE app.tb_folder (
    pk_folder BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    tenant_id UUID NOT NULL,
    fk_parent_folder BIGINT REFERENCES app.tb_folder (pk_folder),
    name TEXT NOT NULL
);

CREATE TABLE app.tb_locale (
    pk_locale BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    code TEXT NOT NULL UNIQUE
);

CREATE TABLE app.tl_project (
    fk_project BIGINT NOT NULL REFERENCES app.tb_project (pk_project),
    fk_locale BIGINT NOT NULL REFERENCES app.tb_locale (pk_locale),
    label TEXT NOT NULL,
    tenant_id UUID NOT NULL
);

CREATE TABLE app.tb_membership (
    pk_membership BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    tenant_id BIGINT NOT NULL REFERENCES app.tb_organization (pk_organization),
    fk_contact BIGINT NOT NULL REFERENCES app.tb_contact (pk_contact)
);

CREATE TABLE app.tb_audit (
    pk_audit BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    tenant_id UUID,
    fk_contact BIGINT REFERENCES app.tb_contact (pk_contact),
    note TEXT NOT NULL
);

CREATE SCHEMA prep_seed;

CREATE TABLE prep_seed.tb_organization (
    id UUID, identifier TEXT, name VARCHAR(50), fk_parent_organization_id UUID
);
CREATE TABLE prep_seed.tb_category (id UUID, identifier TEXT, name TEXT);
CREATE TABLE prep_seed.tb_contact (
    id UUID, identifier TEXT, tenant_id UUID, fk_organization_id UUID, fk_category_id UUID,
    email TEXT, tenant_label UUID
);
CREATE TABLE prep_seed.tb_project (id UUID, identifier TEXT, tenant_id UUID, name TEXT);
CREATE TABLE prep_seed.tb_task (
    id UUID, identifier TEXT, fk_project_id UUID, fk_assignee_id UUID, title TEXT
);
CREATE TABLE prep_seed.tb_folder (
    id UUID, identifier TEXT, tenant_id UUID, fk_parent_folder_id UUID, name TEXT
);
CREATE TABLE prep_seed.tl_project (
    fk_project_id UUID, fk_locale_id UUID, label TEXT, tenant_id UUID
);
CREATE TABLE prep_seed.tb_membership (
    id UUID, identifier TEXT, tenant_id_id UUID, fk_contact_id UUID
);
CREATE TABLE prep_seed.tb_audit (
    id UUID, identifier TEXT, tenant_id UUID, fk_contact_id UUID, note TEXT
);
"""

TENANTS_CODES = {
    "app.tb_organization": 0x10111213,
    "app.tb_category": 0x11121314,
    "app.tb_contact": 0x12131415,
    "app.tb_project": 0x13141516,
    "app.tb_task": 0x14151617,
    "app.tb_folder": 0x15161718,
    "app.tb_audit": 0x16171819,
    "app.tl_project": 0x17181920,
    "app.tb_membership": 0x18192021,
}
