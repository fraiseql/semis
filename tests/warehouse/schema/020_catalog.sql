-- A warehouse: units and categories, suppliers, the products they supply (each variant
-- hanging from a base product), and the items in stock.

CREATE TABLE catalog.tb_unit (
    id UUID DEFAULT gen_random_uuid() NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    pk_unit BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT,
    symbol VARCHAR(8),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID,
    deleted_at TIMESTAMPTZ,
    deleted_by UUID
);

CREATE TABLE catalog.tb_category (
    id UUID DEFAULT gen_random_uuid() NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    pk_category BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID,
    deleted_at TIMESTAMPTZ,
    deleted_by UUID
);

CREATE TABLE catalog.tb_supplier (
    id UUID DEFAULT gen_random_uuid() NOT NULL UNIQUE,
    pk_supplier BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fk_account BIGINT REFERENCES sales.tb_account(pk_account),
    identifier TEXT NOT NULL UNIQUE,
    company_name TEXT NOT NULL,
    country_code CHAR(2) NOT NULL,
    email_address TEXT,
    code CHAR(3) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID,
    deleted_at TIMESTAMPTZ,
    deleted_by UUID
);

CREATE TABLE catalog.tb_product (
    id UUID DEFAULT gen_random_uuid() NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    pk_product BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fk_supplier BIGINT NOT NULL REFERENCES catalog.tb_supplier(pk_supplier),
    fk_category BIGINT NOT NULL REFERENCES catalog.tb_category(pk_category),
    fk_unit BIGINT NOT NULL REFERENCES catalog.tb_unit(pk_unit),
    fk_brand BIGINT REFERENCES catalog.tb_brand(pk_brand),
    fk_certification BIGINT REFERENCES catalog.tb_certification(pk_certification),
    fk_base_product BIGINT REFERENCES catalog.tb_product(pk_product),
    name TEXT NOT NULL,
    launched_on DATE,
    weight_grams INTEGER,
    slug TEXT GENERATED ALWAYS AS (lower(name)) STORED,
    is_base BOOLEAN GENERATED ALWAYS AS (fk_base_product IS NULL) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID,
    deleted_at TIMESTAMPTZ,
    deleted_by UUID,

    CONSTRAINT uq_product_slug_supplier_brand UNIQUE (slug, fk_supplier, fk_brand)
);

CREATE TABLE inventory.tb_item (
    id UUID DEFAULT gen_random_uuid() NOT NULL UNIQUE,
    warehouse_id UUID NOT NULL,
    identifier TEXT NOT NULL UNIQUE,
    pk_item BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fk_account BIGINT REFERENCES sales.tb_account(pk_account),
    fk_order BIGINT REFERENCES sales.tb_order(pk_order),
    fk_product BIGINT NOT NULL REFERENCES catalog.tb_product(pk_product),
    received_at TIMESTAMPTZ,
    shelved_at TIMESTAMPTZ,
    first_counted_at TIMESTAMPTZ,
    latest_counted_at TIMESTAMPTZ,
    serial_number TEXT NOT NULL,
    supplier_reference TEXT,
    account_reference TEXT,
    disposed_at TIMESTAMPTZ,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID,
    deleted_at TIMESTAMPTZ,
    deleted_by UUID,

    CONSTRAINT uq_item_account_serial UNIQUE (fk_account, serial_number)
);
