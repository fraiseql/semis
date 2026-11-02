CREATE SCHEMA semis_compat;

CREATE TYPE semis_compat.status AS ENUM ('draft', 'live', 'gone');

CREATE TABLE semis_compat.tb_continent (
    pk_continent BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name VARCHAR(50) NOT NULL
);

INSERT INTO semis_compat.tb_continent (id, identifier, name) VALUES
    ('00000000-0000-4000-8000-000000000001', 'europe', 'Europe'),
    ('00000000-0000-4000-8000-000000000002', 'asia', 'Asia');

CREATE TABLE semis_compat.tb_country (
    pk_country BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_continent BIGINT NOT NULL REFERENCES semis_compat.tb_continent (pk_continent),
    iso_code CHAR(2) NOT NULL,
    note TEXT,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE semis_compat.tb_city (
    pk_city BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_country BIGINT NOT NULL REFERENCES semis_compat.tb_country (pk_country),
    name VARCHAR(40) NOT NULL,
    status semis_compat.status NOT NULL DEFAULT 'draft',
    kind semis_compat.status,
    population INTEGER NOT NULL
);

CREATE TABLE semis_compat.tb_location (
    pk_location BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_parent_location BIGINT REFERENCES semis_compat.tb_location (pk_location),
    name VARCHAR(50) NOT NULL
);
