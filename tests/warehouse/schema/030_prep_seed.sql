-- The staging twins: the same tables in prep_seed, every foreign key a UUID named
-- <fk>_id, no REFERENCES, no identity and no generated column.

CREATE TABLE prep_seed.tb_unit (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    identifier TEXT NOT NULL,
    name TEXT,
    symbol VARCHAR(8),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID,
    deleted_at TIMESTAMPTZ,
    deleted_by UUID,
    UNIQUE (id)
);

CREATE TABLE prep_seed.tb_category (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    identifier TEXT NOT NULL,
    name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID,
    deleted_at TIMESTAMPTZ,
    deleted_by UUID,
    UNIQUE (id)
);

CREATE TABLE prep_seed.tb_supplier (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    fk_account_id UUID,
    identifier TEXT NOT NULL,
    company_name TEXT NOT NULL,
    country_code CHAR(2) NOT NULL,
    email_address TEXT,
    code CHAR(3) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID,
    deleted_at TIMESTAMPTZ,
    deleted_by UUID,
    UNIQUE (id)
);

CREATE TABLE prep_seed.tb_product (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    identifier TEXT NOT NULL,
    fk_supplier_id UUID NOT NULL,
    fk_category_id UUID NOT NULL,
    fk_unit_id UUID NOT NULL,
    fk_brand_id UUID,
    fk_certification_id UUID,
    fk_base_product_id UUID,
    name TEXT NOT NULL,
    launched_on DATE,
    weight_grams INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID,
    deleted_at TIMESTAMPTZ,
    deleted_by UUID,
    UNIQUE (id)
);

CREATE TABLE prep_seed.tb_item (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    warehouse_id UUID NOT NULL,
    identifier TEXT NOT NULL,
    fk_account_id UUID,
    fk_order_id UUID,
    fk_product_id UUID NOT NULL,
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
    UNIQUE (id)
);
