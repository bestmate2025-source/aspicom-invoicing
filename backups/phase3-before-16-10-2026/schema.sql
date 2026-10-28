-- schema.sql — Aspicom LLC Invoice Management System
-- MySQL 8.0+. InnoDB throughout (needed for foreign keys + row-level locking).
--
-- Design notes (read before running):
--
-- 1. `invoices.invoice_number` is UNIQUE. This enforces the duplicate-
--    handling rule from Stage 0 at the database level — a plain INSERT of a
--    repeated invoice_number will raise a MySQL duplicate-key error. The
--    Flask layer catches that and turns it into "skip with warning" by
--    default, or does an explicit UPDATE instead of INSERT when --force is
--    passed. The DB constraint is the backstop; the app decides the UX.
--
-- 2. Money columns are DECIMAL(12,2), never FLOAT — avoids floating-point
--    rounding errors on totals. `invoice_items.qty` is DECIMAL(10,2) rather
--    than INT in case a future product is sold by fractional unit (weight,
--    volume) — whole-unit sales (2 vials, 5 boxes) still store cleanly as
--    2.00 / 5.00.
--
-- 3. `company` is a single-row settings table by convention (always id=1).
--    The app does an upsert (INSERT ... ON DUPLICATE KEY UPDATE) against
--    id=1 rather than allowing multiple rows — enforced by application
--    logic, not a DB constraint, since MySQL has no clean native way to cap
--    a table at exactly one row without a trigger. Flagging this rather
--    than silently relying on convention.
--
-- 4. `parsed_invoices.raw_json` stores the full ParsedInvoice.as_dict()
--    output from Stage 2's parser — nothing is lost even if the structured
--    columns don't capture every nuance of a given supplier's layout.
--
-- 5. Foreign keys use ON DELETE CASCADE for invoice_items (an invoice's line
--    items are meaningless without their parent) and ON DELETE RESTRICT for
--    invoices.client_id (prevents silently orphaning/deleting invoice
--    history if someone deletes a client — the app should require
--    reassigning or archiving invoices first).

CREATE DATABASE IF NOT EXISTS aspicom_invoicing
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE aspicom_invoicing;

-- ---------------------------------------------------------------------
-- clients
-- ---------------------------------------------------------------------
CREATE TABLE clients (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(255)    NOT NULL,
    address     TEXT,
    trn         VARCHAR(50),
    email       VARCHAR(255),
    created_at  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_clients_email (email)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- invoices
-- ---------------------------------------------------------------------
CREATE TABLE invoices (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    client_id       INT UNSIGNED    NOT NULL,
    lpo_number      VARCHAR(100),
    invoice_number  VARCHAR(100)    NOT NULL,
    issued          DATE,
    due             DATE,
    status          ENUM('Draft', 'Sent', 'Paid', 'Overdue') NOT NULL DEFAULT 'Draft',
    vat_percent     DECIMAL(5,2)    NOT NULL DEFAULT 0.00,
    notes           TEXT,
    subtotal        DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
    vat             DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
    total           DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_invoices_invoice_number UNIQUE (invoice_number),
    CONSTRAINT fk_invoices_client
        FOREIGN KEY (client_id) REFERENCES clients(id)
        ON DELETE RESTRICT ON UPDATE CASCADE,

    INDEX idx_invoices_status (status),
    INDEX idx_invoices_due (due),
    INDEX idx_invoices_client (client_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- invoice_items
-- ---------------------------------------------------------------------
CREATE TABLE invoice_items (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    invoice_id      INT UNSIGNED    NOT NULL,
    product_name    VARCHAR(255)    NOT NULL,
    brand           VARCHAR(255),
    unit_price      DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
    qty             DECIMAL(10,2)   NOT NULL DEFAULT 0.00,
    amount          DECIMAL(12,2)   NOT NULL DEFAULT 0.00,

    CONSTRAINT fk_invoice_items_invoice
        FOREIGN KEY (invoice_id) REFERENCES invoices(id)
        ON DELETE CASCADE ON UPDATE CASCADE,

    INDEX idx_invoice_items_invoice (invoice_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- inventory
-- ---------------------------------------------------------------------
CREATE TABLE inventory (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    product     VARCHAR(255)    NOT NULL,
    brand       VARCHAR(255),
    price       DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
    par_level   DECIMAL(10,2)   NOT NULL DEFAULT 0.00,
    balance     DECIMAL(10,2)   NOT NULL DEFAULT 0.00,
    updated_at  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT uq_inventory_product UNIQUE (product),
    INDEX idx_inventory_balance (balance)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- company  (single-row settings table — always id = 1; enforced by the
-- application layer via upsert, not by a DB constraint. See note 3 above.)
-- ---------------------------------------------------------------------
CREATE TABLE company (
    id          TINYINT UNSIGNED PRIMARY KEY DEFAULT 1,
    name        VARCHAR(255),
    address     TEXT,
    trn         VARCHAR(50),
    phone       VARCHAR(50),
    email       VARCHAR(255),
    website     VARCHAR(255),
    signee      VARCHAR(255),
    logo_path   VARCHAR(500),
    stamp_path  VARCHAR(500)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- parsed_invoices  (raw log of every supplier PDF ingested via Stage 2's
-- parser — kept separate from `invoices`, which holds YOUR outgoing
-- invoices to your own clients. This table is the incoming/supplier side.)
-- ---------------------------------------------------------------------
CREATE TABLE parsed_invoices (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    source_pdf      VARCHAR(500)    NOT NULL,
    supplier_name   VARCHAR(255),
    header_trn      VARCHAR(50),
    lpo_number      VARCHAR(100),
    invoice_number  VARCHAR(100),
    date            DATE,
    subtotal        DECIMAL(12,2),
    vat             DECIMAL(12,2),
    grand_total     DECIMAL(12,2),
    raw_json        JSON,
    imported_at     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_parsed_invoices_invoice_number (invoice_number),
    INDEX idx_parsed_invoices_supplier (supplier_name),
    CONSTRAINT uq_supplier_invoice UNIQUE (supplier_name, invoice_number)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- import_log  (every PDF import attempt — success, failure, duplicate.
-- 'ocr_fallback' status is kept in the enum for forward-compatibility even
-- though the current parser has no OCR path; unused until/unless OCR
-- returns, but costs nothing to reserve now vs. an ALTER TABLE later.)
-- ---------------------------------------------------------------------
CREATE TABLE import_log (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    source_pdf  VARCHAR(500) NOT NULL,
    status      ENUM('success','failed','duplicate','ocr_fallback') NOT NULL,
    message     TEXT,
    imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_import_log_status (status)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- invoice_audit  (field-level audit trail for invoice edits. No FK to
-- invoices — deliberately: a 'delete' audit row must survive even after
-- the invoice row itself is gone, which a FOREIGN KEY ... ON DELETE CASCADE
-- would prevent. invoice_id is kept as a plain indexed column instead.)
-- ---------------------------------------------------------------------
CREATE TABLE invoice_audit (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    invoice_id  INT UNSIGNED NOT NULL,
    action      ENUM('create','update','delete','status_change') NOT NULL,
    field_name  VARCHAR(100),
    old_value   TEXT,
    new_value   TEXT,
    changed_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_audit_invoice (invoice_id),
    INDEX idx_audit_date (changed_at)
) ENGINE=InnoDB;
