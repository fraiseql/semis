-- The parents the warehouse chain references but its scenario leaves NULL: only the key
-- each foreign key points at, so the references resolve.
CREATE SCHEMA catalog;
CREATE SCHEMA inventory;
CREATE SCHEMA sales;
CREATE SCHEMA prep_seed;

CREATE TABLE sales.tb_account (pk_account BIGINT PRIMARY KEY, id UUID NOT NULL UNIQUE);
CREATE TABLE sales.tb_order (pk_order BIGINT PRIMARY KEY, id UUID NOT NULL UNIQUE);
CREATE TABLE catalog.tb_brand (pk_brand BIGINT PRIMARY KEY, id UUID NOT NULL UNIQUE);
CREATE TABLE catalog.tb_certification (pk_certification BIGINT PRIMARY KEY, id UUID NOT NULL UNIQUE);
