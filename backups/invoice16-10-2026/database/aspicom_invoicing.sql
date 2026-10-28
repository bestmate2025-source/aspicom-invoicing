-- Backup of aspicom_invoicing on 2026-10-16T11:46:45.285404
-- Tables: clients, company, import_log, inventory, invoice_audit, invoice_items, invoice_payments, invoices, parsed_invoices, users

SET FOREIGN_KEY_CHECKS = 0;

-- Table: clients
DROP TABLE IF EXISTS `clients`;
CREATE TABLE `clients` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `address` text COLLATE utf8mb4_unicode_ci,
  `po_box` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `trn` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_clients_email` (`email`)
) ENGINE=InnoDB AUTO_INCREMENT=56 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Data for clients (6 rows)
INSERT INTO `clients` VALUES (1, 'Console Test Client', 'Abu Dhabi, UAE', NULL, '100999999900003', 'ctest@example.com', '2026-09-28 11:50:45');
INSERT INTO `clients` VALUES (2, 'Console Test Client', 'Abu Dhabi, UAE', NULL, '100999999900003', 'ctest@example.com', '2026-09-28 11:51:06');
INSERT INTO `clients` VALUES (3, 'SOFTSOLUTIONC CHEPTECH', '808 DEWAN AL KHOR TOWER', NULL, '2525', 'bestmate2025@gmail.com', '2026-09-28 12:15:28');
INSERT INTO `clients` VALUES (4, 'Test Client', 'B115 OWN HOMES', NULL, '2525000', 'bestmate202625@gmail.com', '2026-09-29 08:49:12');
INSERT INTO `clients` VALUES (5, 'Test PO Box Client', 'Al Ain, UAE', '99999', '100999999900003', 'test@example.com', '2026-10-03 06:52:35');
INSERT INTO `clients` VALUES (6, 'Test PO Box Client', 'Al Ain, UAE', '99999', '100999999900003', 'test@example.com', '2026-10-03 06:59:43');

-- Table: company
DROP TABLE IF EXISTS `company`;
CREATE TABLE `company` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `address` text COLLATE utf8mb4_unicode_ci,
  `po_box` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `trn` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `phone` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `website` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `signee` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `logo_path` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `stamp_path` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=55 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Data for company (1 rows)
INSERT INTO `company` VALUES (1, 'DDA', 'Office # 10, Level 1, Sharjah Media City, Sharja51h, UAE', '515000', '100441082300003', '+971 527600033', 'Info@aspicomllc.com', 'www.aspicomllc.com', 'Accounts', 'uploads/logo.png', 'uploads/stamp.png');

-- Table: import_log
DROP TABLE IF EXISTS `import_log`;
CREATE TABLE `import_log` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `source_pdf` varchar(500) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` enum('success','failed','duplicate','ocr_fallback') COLLATE utf8mb4_unicode_ci NOT NULL,
  `message` text COLLATE utf8mb4_unicode_ci,
  `imported_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_import_log_status` (`status`)
) ENGINE=InnoDB AUTO_INCREMENT=52 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Data for import_log (3 rows)
INSERT INTO `import_log` VALUES (1, 'NMC ROSITA correct one A.pdf', 'success', 'Imported invoice_number=''14142026'' from supplier=''NMC WHOLESALES DRUG STORE AND SUPPLIES L.L.C''.', '2026-09-28 13:23:59');
INSERT INTO `import_log` VALUES (2, 'NMC ROSITA correct one A.pdf', 'duplicate', 'Already imported as parsed_invoices.id=1 (supplier=''NMC WHOLESALES DRUG STORE AND SUPPLIES L.L.C'', invoice_number=''14142026'').', '2026-09-28 13:24:30');
INSERT INTO `import_log` VALUES (3, 'NMC ROSITA correct one A.pdf', 'duplicate', 'Already imported as parsed_invoices.id=1 (supplier=''NMC WHOLESALES DRUG STORE AND SUPPLIES L.L.C'', invoice_number=''14142026'').', '2026-09-28 13:43:14');

-- Table: inventory
DROP TABLE IF EXISTS `inventory`;
CREATE TABLE `inventory` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `product` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `brand` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `price` decimal(12,2) NOT NULL DEFAULT '0.00',
  `vat_percent` decimal(5,2) NOT NULL DEFAULT '5.00',
  `par_level` decimal(10,2) NOT NULL DEFAULT '0.00',
  `balance` decimal(10,2) NOT NULL DEFAULT '0.00',
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_inventory_product` (`product`),
  KEY `idx_inventory_balance` (`balance`)
) ENGINE=InnoDB AUTO_INCREMENT=6 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Data for inventory (5 rows)
INSERT INTO `inventory` VALUES (1, 'cream', 'darma', '250.00', '5.00', '330.00', '350.00', '2026-09-28 14:04:34');
INSERT INTO `inventory` VALUES (2, 'Test Product', 'Test Product', '100.00', '5.00', '5.00', '0.00', '2026-10-04 20:58:48');
INSERT INTO `inventory` VALUES (3, 'Test Product3', 'Test Product3', '200.00', '5.00', '20.00', '19.00', '2026-10-08 18:14:13');
INSERT INTO `inventory` VALUES (4, 'Test Product4', 'Test Product4', '200.00', '5.00', '20.00', '18.00', '2026-10-08 18:14:39');
INSERT INTO `inventory` VALUES (5, 'Test Product5', 'Test Product5', '200.00', '5.00', '20.00', '15.00', '2026-10-08 18:14:56');

-- Table: invoice_audit
DROP TABLE IF EXISTS `invoice_audit`;
CREATE TABLE `invoice_audit` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `invoice_id` int unsigned NOT NULL,
  `action` enum('create','update','delete','status_change') COLLATE utf8mb4_unicode_ci NOT NULL,
  `field_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `old_value` text COLLATE utf8mb4_unicode_ci,
  `new_value` text COLLATE utf8mb4_unicode_ci,
  `changed_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_audit_invoice` (`invoice_id`),
  KEY `idx_audit_date` (`changed_at`)
) ENGINE=InnoDB AUTO_INCREMENT=103 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Data for invoice_audit (33 rows)
INSERT INTO `invoice_audit` VALUES (1, 1, 'create', NULL, NULL, 'invoice_number=2231210, total=525.00', '2026-09-29 08:51:05');
INSERT INTO `invoice_audit` VALUES (2, 1, 'update', 'amount_paid', '0.00', '200.00', '2026-09-29 17:53:11');
INSERT INTO `invoice_audit` VALUES (3, 1, 'update', 'client_id', '4', '2', '2026-09-29 17:54:17');
INSERT INTO `invoice_audit` VALUES (4, 1, 'update', 'invoice_number', '2231210', '223121', '2026-09-29 17:54:17');
INSERT INTO `invoice_audit` VALUES (5, 1, 'update', 'due', '2026-09-01', '2026-09-30', '2026-09-29 17:54:17');
INSERT INTO `invoice_audit` VALUES (6, 1, 'status_change', 'status', 'Sent', 'Paid', '2026-09-29 17:54:17');
INSERT INTO `invoice_audit` VALUES (7, 1, 'update', 'vat_percent', '5.00', '0', '2026-09-29 17:54:17');
INSERT INTO `invoice_audit` VALUES (8, 1, 'update', 'subtotal', '500.00', '1000.00', '2026-09-29 17:54:17');
INSERT INTO `invoice_audit` VALUES (9, 1, 'update', 'vat', '25.00', '0.00', '2026-09-29 17:54:17');
INSERT INTO `invoice_audit` VALUES (10, 1, 'update', 'total', '525.00', '1000.00', '2026-09-29 17:54:17');
INSERT INTO `invoice_audit` VALUES (11, 1, 'update', 'items', '[{''product_name'': ''Test Product'', ''brand'': ''Test Product'', ''unit_price'': ''100.00'', ''qty'': ''5.00''}]', '[{''product_name'': ''Test Product'', ''brand'': ''Test Product'', ''unit_price'': ''100'', ''qty'': ''10''}]', '2026-09-29 17:54:17');
INSERT INTO `invoice_audit` VALUES (12, 1, 'update', 'amount_paid', '200.00', '500.00', '2026-09-29 17:55:24');
INSERT INTO `invoice_audit` VALUES (13, 1, 'delete', 'amount_paid', '500.00', '200.00', '2026-09-29 17:56:41');
INSERT INTO `invoice_audit` VALUES (14, 1, 'delete', 'amount_paid', '200.00', '0.00', '2026-09-29 17:56:43');
INSERT INTO `invoice_audit` VALUES (15, 2, 'create', NULL, NULL, 'invoice_number=test-1000, total=1050.00', '2026-09-29 19:00:09');
INSERT INTO `invoice_audit` VALUES (16, 2, 'update', 'amount_paid', '0.00', '300.00', '2026-09-29 20:20:46');
INSERT INTO `invoice_audit` VALUES (17, 2, 'status_change', 'status', 'Sent', 'Paid', '2026-09-29 20:21:05');
INSERT INTO `invoice_audit` VALUES (18, 2, 'update', 'items', '[{''product_name'': ''Test Product'', ''brand'': ''Test Product'', ''unit_price'': ''100.00'', ''qty'': ''10.00''}]', '[{''product_name'': ''Test Product'', ''brand'': ''Test Product'', ''unit_price'': ''100'', ''qty'': ''10''}]', '2026-09-29 20:21:05');
INSERT INTO `invoice_audit` VALUES (19, 3, 'create', NULL, NULL, 'invoice_number=ASPI01102026-04, total=1018.50', '2026-10-02 11:34:09');
INSERT INTO `invoice_audit` VALUES (20, 4, 'create', NULL, NULL, 'invoice_number=223121-8580, total=1029.00', '2026-10-03 06:13:08');
INSERT INTO `invoice_audit` VALUES (21, 3, 'update', 'amount_paid', '0.00', '500.00', '2026-10-03 06:16:15');
INSERT INTO `invoice_audit` VALUES (22, 3, 'update', 'items', '[{''product_name'': ''Test Product'', ''brand'': ''Test Product'', ''unit_price'': ''100.00'', ''qty'': ''10.00'', ''foc_qty'': ''10.00'', ''discount_percent'': ''3.00'', ''discount_amount'': ''30.00'', ''amount'': ''1000.00'', ''vat_percent'': ''5.00'', ''vat_amount'': ''48.50''}]', '[{''product_name'': ''Test Product'', ''brand'': ''Test Product'', ''unit_price'': ''100'', ''qty'': ''10'', ''foc_qty'': ''10'', ''discount_percent'': ''3'', ''discount_amount'': ''None'', ''amount'': ''None'', ''vat_percent'': ''5'', ''vat_amount'': ''None''}]', '2026-10-03 06:16:19');
INSERT INTO `invoice_audit` VALUES (23, 5, 'create', NULL, NULL, 'quotation=QTN-2026-10-03:0001, total=210.00', '2026-10-03 07:00:43');
INSERT INTO `invoice_audit` VALUES (24, 6, 'create', NULL, NULL, 'quotation=QTN-2026-10-03:0002, total=210.00', '2026-10-03 07:00:54');
INSERT INTO `invoice_audit` VALUES (25, 7, 'create', NULL, NULL, 'quotation=QTN-2026-10-03:0003, total=210.00', '2026-10-03 07:02:52');
INSERT INTO `invoice_audit` VALUES (26, 8, 'create', NULL, NULL, 'quotation=QTN-2026-10-03:0004, total=210.00', '2026-10-03 07:04:40');
INSERT INTO `invoice_audit` VALUES (27, 9, 'create', NULL, NULL, 'invoice=INV-2026-10-03:0001, total=315.00', '2026-10-03 07:06:00');
INSERT INTO `invoice_audit` VALUES (28, 10, 'create', NULL, NULL, 'invoice=INV-2026-10-03:0002, total=315.00', '2026-10-03 13:33:31');
INSERT INTO `invoice_audit` VALUES (29, 11, 'create', NULL, NULL, 'invoice=INV-2026-10-04:0001, total=10.50', '2026-10-04 20:48:26');
INSERT INTO `invoice_audit` VALUES (30, 12, 'create', NULL, NULL, 'invoice=INV-2026-10-04:0002, total=10.50', '2026-10-04 20:49:04');
INSERT INTO `invoice_audit` VALUES (31, 13, 'create', NULL, NULL, 'invoice=INV-2026-10-04:0001, total=1029.00', '2026-10-04 20:58:48');
INSERT INTO `invoice_audit` VALUES (52, 62, 'create', NULL, NULL, 'quotation=QTN-2026-10-06:0001, total=21.00', '2026-10-06 06:22:52');
INSERT INTO `invoice_audit` VALUES (53, 62, 'update', 'currency', 'AED', 'USD', '2026-10-06 06:22:52');

-- Table: invoice_items
DROP TABLE IF EXISTS `invoice_items`;
CREATE TABLE `invoice_items` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `invoice_id` int unsigned NOT NULL,
  `product_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `brand` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `unit_price` decimal(12,2) NOT NULL DEFAULT '0.00',
  `qty` decimal(10,2) NOT NULL DEFAULT '0.00',
  `foc_qty` decimal(10,2) NOT NULL DEFAULT '0.00',
  `discount_percent` decimal(5,2) NOT NULL DEFAULT '0.00',
  `discount_amount` decimal(12,2) NOT NULL DEFAULT '0.00',
  `amount` decimal(12,2) NOT NULL DEFAULT '0.00',
  `vat_percent` decimal(5,2) NOT NULL DEFAULT '0.00',
  `vat_amount` decimal(12,2) NOT NULL DEFAULT '0.00',
  PRIMARY KEY (`id`),
  KEY `idx_invoice_items_invoice` (`invoice_id`),
  CONSTRAINT `fk_invoice_items_invoice` FOREIGN KEY (`invoice_id`) REFERENCES `invoices` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=107 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Data for invoice_items (10 rows)
INSERT INTO `invoice_items` VALUES (2, 1, 'Test Product', 'Test Product', '100.00', '10.00', '0.00', '0.00', '0.00', '1000.00', '0.00', '0.00');
INSERT INTO `invoice_items` VALUES (4, 2, 'Test Product', 'Test Product', '100.00', '10.00', '0.00', '0.00', '0.00', '1000.00', '0.00', '0.00');
INSERT INTO `invoice_items` VALUES (6, 4, 'Test Product', 'Test Product', '100.00', '10.00', '1.00', '2.00', '20.00', '1000.00', '5.00', '49.00');
INSERT INTO `invoice_items` VALUES (7, 3, 'Test Product', 'Test Product', '100.00', '10.00', '10.00', '3.00', '30.00', '1000.00', '5.00', '48.50');
INSERT INTO `invoice_items` VALUES (8, 5, 'Test Product', NULL, '100.00', '2.00', '0.00', '0.00', '0.00', '200.00', '5.00', '10.00');
INSERT INTO `invoice_items` VALUES (9, 6, 'Test Product', NULL, '100.00', '2.00', '0.00', '0.00', '0.00', '200.00', '5.00', '10.00');
INSERT INTO `invoice_items` VALUES (10, 7, 'Test Product', NULL, '100.00', '2.00', '0.00', '0.00', '0.00', '200.00', '5.00', '10.00');
INSERT INTO `invoice_items` VALUES (11, 8, 'Test Product', NULL, '100.00', '2.00', '0.00', '0.00', '0.00', '200.00', '5.00', '10.00');
INSERT INTO `invoice_items` VALUES (12, 9, 'Test Product', NULL, '100.00', '3.00', '0.00', '0.00', '0.00', '300.00', '5.00', '15.00');
INSERT INTO `invoice_items` VALUES (13, 10, 'Test Product', NULL, '100.00', '3.00', '0.00', '0.00', '0.00', '300.00', '5.00', '15.00');

-- Table: invoice_payments
DROP TABLE IF EXISTS `invoice_payments`;
CREATE TABLE `invoice_payments` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `invoice_id` int unsigned NOT NULL,
  `amount` decimal(12,2) NOT NULL,
  `paid_date` date NOT NULL,
  `payment_method` enum('cash','bank_transfer','card','cheque','other') COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `reference` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `notes` text COLLATE utf8mb4_unicode_ci,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_payment_invoice` (`invoice_id`),
  KEY `idx_payment_date` (`paid_date`),
  CONSTRAINT `fk_payment_invoice` FOREIGN KEY (`invoice_id`) REFERENCES `invoices` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=52 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Data for invoice_payments (2 rows)
INSERT INTO `invoice_payments` VALUES (3, 2, '300.00', '2026-09-30', 'bank_transfer', 'rere', NULL, '2026-09-29 20:20:46');
INSERT INTO `invoice_payments` VALUES (4, 3, '500.00', '2026-10-03', 'cash', NULL, NULL, '2026-10-03 06:16:15');

-- Table: invoices
DROP TABLE IF EXISTS `invoices`;
CREATE TABLE `invoices` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `company_id` int unsigned NOT NULL DEFAULT '1',
  `currency` varchar(3) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'USD',
  `document_type` enum('invoice','quotation') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'invoice',
  `client_id` int unsigned NOT NULL,
  `lpo_number` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `invoice_number` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `issued` date DEFAULT NULL,
  `due` date DEFAULT NULL,
  `valid_until` date DEFAULT NULL,
  `status` enum('Draft','Sent','Paid','Overdue','Accepted','Rejected') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'Draft',
  `vat_percent` decimal(5,2) NOT NULL DEFAULT '0.00',
  `notes` text COLLATE utf8mb4_unicode_ci,
  `subtotal` decimal(12,2) NOT NULL DEFAULT '0.00',
  `vat` decimal(12,2) NOT NULL DEFAULT '0.00',
  `total` decimal(12,2) NOT NULL DEFAULT '0.00',
  `amount_paid` decimal(12,2) NOT NULL DEFAULT '0.00',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_invoices_invoice_number` (`invoice_number`),
  KEY `idx_invoices_status` (`status`),
  KEY `idx_invoices_due` (`due`),
  KEY `idx_invoices_client` (`client_id`),
  KEY `idx_document_type` (`document_type`),
  KEY `idx_invoices_company` (`company_id`),
  CONSTRAINT `fk_invoices_client` FOREIGN KEY (`client_id`) REFERENCES `clients` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=113 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Data for invoices (10 rows)
INSERT INTO `invoices` VALUES (1, 1, 'USD', 'invoice', 2, '202101', '223121', '2026-09-29', '2026-09-30', NULL, 'Sent', '0.00', NULL, '1000.00', '0.00', '1000.00', '0.00', '2026-09-29 08:51:05');
INSERT INTO `invoices` VALUES (2, 1, 'USD', 'invoice', 4, '1000-test', 'test-1000', '2026-09-29', '2026-10-05', NULL, 'Paid', '5.00', NULL, '1000.00', '50.00', '1050.00', '300.00', '2026-09-29 19:00:09');
INSERT INTO `invoices` VALUES (3, 1, 'USD', 'invoice', 4, 'PO7200000007244910', 'ASPI01102026-04', '2026-10-02', '2026-10-21', NULL, 'Sent', '0.00', NULL, '1000.00', '48.50', '1018.50', '500.00', '2026-10-02 11:34:09');
INSERT INTO `invoices` VALUES (4, 1, 'USD', 'invoice', 3, '202101-8585', '223121-8580', '2026-10-03', '2026-09-30', NULL, 'Sent', '0.00', NULL, '1000.00', '49.00', '1029.00', '0.00', '2026-10-03 06:13:08');
INSERT INTO `invoices` VALUES (5, 1, 'USD', 'quotation', 1, NULL, 'QTN-2026-10-03:0001', '2026-10-03', NULL, '2026-11-03', 'Draft', '5.00', NULL, '200.00', '10.00', '210.00', '0.00', '2026-10-03 07:00:43');
INSERT INTO `invoices` VALUES (6, 1, 'USD', 'quotation', 1, NULL, 'QTN-2026-10-03:0002', '2026-10-03', NULL, '2026-11-03', 'Draft', '5.00', NULL, '200.00', '10.00', '210.00', '0.00', '2026-10-03 07:00:54');
INSERT INTO `invoices` VALUES (7, 1, 'USD', 'quotation', 1, NULL, 'QTN-2026-10-03:0003', '2026-10-03', NULL, '2026-11-03', 'Draft', '5.00', NULL, '200.00', '10.00', '210.00', '0.00', '2026-10-03 07:02:52');
INSERT INTO `invoices` VALUES (8, 1, 'USD', 'quotation', 1, NULL, 'QTN-2026-10-03:0004', '2026-10-03', NULL, '2026-11-03', 'Draft', '5.00', NULL, '200.00', '10.00', '210.00', '0.00', '2026-10-03 07:04:40');
INSERT INTO `invoices` VALUES (9, 1, 'USD', 'invoice', 1, NULL, 'INV-2026-10-03:0001', '2026-10-03', '2026-11-03', NULL, 'Draft', '5.00', NULL, '300.00', '15.00', '315.00', '0.00', '2026-10-03 07:06:00');
INSERT INTO `invoices` VALUES (10, 1, 'USD', 'invoice', 1, NULL, 'INV-2026-10-03:0002', '2026-10-03', '2026-11-03', NULL, 'Draft', '5.00', NULL, '300.00', '15.00', '315.00', '0.00', '2026-10-03 13:33:31');

-- Table: parsed_invoices
DROP TABLE IF EXISTS `parsed_invoices`;
CREATE TABLE `parsed_invoices` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `source_pdf` varchar(500) COLLATE utf8mb4_unicode_ci NOT NULL,
  `supplier_name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `header_trn` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `lpo_number` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `invoice_number` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `date` date DEFAULT NULL,
  `subtotal` decimal(12,2) DEFAULT NULL,
  `vat` decimal(12,2) DEFAULT NULL,
  `grand_total` decimal(12,2) DEFAULT NULL,
  `raw_json` json DEFAULT NULL,
  `imported_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_supplier_invoice` (`supplier_name`,`invoice_number`),
  KEY `idx_parsed_invoices_invoice_number` (`invoice_number`),
  KEY `idx_parsed_invoices_supplier` (`supplier_name`)
) ENGINE=InnoDB AUTO_INCREMENT=52 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Data for parsed_invoices (1 rows)
INSERT INTO `parsed_invoices` VALUES (1, 'NMC ROSITA correct one A.pdf', 'NMC WHOLESALES DRUG STORE AND SUPPLIES L.L.C', '100441082300003', 'PO72000000046762', '14142026', '2026-01-23', '1200.00', '0.00', '1200.00', '{"buyer": null, "vendor": {"trn": "100551136300003", "name": "NMC WHOLESALES DRUG STORE AND SUPPLIES L.L.C", "address": "PO BOX: 23307 BUILDING 11/PLOT 5, LEETAG, 16TH ROAD, INDUSTRIAL AREA SANAIYA, AL-AIN, AL AIN, UNITED ARAB EMIRATES"}, "subject": "INVOICE OF BELOW MENTION PRODUCT.", "date_raw": "23-JAN-2026", "subtotal": "1200.00", "warnings": [], "header_trn": "100441082300003", "line_items": [{"qty": "2", "s_no": "1", "brand": "ASPICOMLLC", "type_": "VIALS", "amount": "1200.00", "unit_price": "600.00", "product_name": "COCKTAIL ASPI WHITNING 5ML * 5 VIALS"}], "lpo_number": "PO72000000046762", "vat_amount": "0.00", "date_parsed": "2026-01-23", "grand_total": "1200.00", "source_path": "C:\\\\Users\\\\farha\\\\AppData\\\\Local\\\\Temp\\\\tmpb6_cmx5y\\\\NMC ROSITA correct one A.pdf", "vat_percent": "0.00", "footer_contact": "Best regards,\\nAccounts\\nAspicom LLC\\nMobile # +971 527600033", "invoice_number": "14142026", "vat_percent_derived": true}', '2026-09-28 13:23:59');

-- Table: users
DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `username` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `password_hash` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `full_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `role` enum('admin','user') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'user',
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `last_login` timestamp NULL DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `is_root` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `username` (`username`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Data for users (3 rows)
INSERT INTO `users` VALUES (1, 'farhan', 'scrypt:32768:8:1$AxxFnESwDOajw9VJ$84cc9cbd3e568dd3100313512c564516c0eac08988be88a8cfb7dfb78f676746c4e8adc9369be22cae9bfd4f66772c0200895821c30058cd71bd37d7d3275621', 'Farhan', NULL, 'admin', 1, '2026-10-14 14:59:28', '2026-10-09 00:18:22', 1);
INSERT INTO `users` VALUES (2, 'usman', 'scrypt:32768:8:1$i724mazsqISd52xU$d31b358b73ff59545136931bb37f0345dab939eae19467fd4f808f930b0698679d0897a340d1602eecfc6b2104e3d541b7bfb3aa5bb6738541cf9c10bae20f4d', 'Usman', NULL, 'user', 1, '2026-10-09 19:35:47', '2026-10-09 00:18:22', 0);
INSERT INTO `users` VALUES (4, 'User', 'scrypt:32768:8:1$F1klwb1FTQYOGNpm$1d53ea21d4285b92bb9733d278262ba76249fc286c7e51595aafe46da7a82622e25e8ac56a621e75386c4ceaaa880d813e03ab3294b815891a152a5b91741641', 'User', 'fahraresources@gmail.com', 'admin', 1, '2026-10-10 05:01:10', '2026-10-10 03:50:57', 0);

SET FOREIGN_KEY_CHECKS = 1;
