-- Larkspur Analytics — synthetic CRM / billing database for the Walnut workspace demo.
-- Every company, person, email and number here is INVENTED (.example domains, 020 7946 0xxx
-- drama-range phone numbers). The same people, customers, projects and invoices appear in the
-- documents under ../files/, so ingestion of both sources produces cross-source links.
--
-- Deliberate asymmetries (see ../MANIFEST.md):
--   * invoice INV-2026-0063 exists ONLY here (no document) — a DB-only fact.
--   * customer C-1003 Tamsinbay Water Authority / project PRJ-103 appear in the DB and only in the
--     '#'-named roadmap folder — lost entirely if that folder is silently skipped.
--   * contact_notes.note is free prose (a column that should be treated as passages, not facts).
--
-- Loaded automatically by docker-entrypoint-initdb.d (infra/docker-compose.walnut.yml).

CREATE TABLE employees (
    employee_id   VARCHAR(12)  PRIMARY KEY,
    full_name     VARCHAR(120) NOT NULL,
    email         VARCHAR(200) NOT NULL UNIQUE,
    title         VARCHAR(120) NOT NULL,
    office        VARCHAR(80)  NOT NULL
);

CREATE TABLE customers (
    customer_id   VARCHAR(12)  PRIMARY KEY,
    name          VARCHAR(160) NOT NULL,
    domain        VARCHAR(120) NOT NULL UNIQUE,
    sector        VARCHAR(60)  NOT NULL,
    city          VARCHAR(80)  NOT NULL,
    account_owner VARCHAR(12)  NOT NULL REFERENCES employees(employee_id),
    created_at    DATE         NOT NULL
);

CREATE TABLE contacts (
    contact_id    SERIAL       PRIMARY KEY,
    customer_id   VARCHAR(12)  NOT NULL REFERENCES customers(customer_id),
    full_name     VARCHAR(120) NOT NULL,
    email         VARCHAR(200) NOT NULL UNIQUE,
    phone         VARCHAR(40),
    role          VARCHAR(120) NOT NULL
);

CREATE TABLE projects (
    project_id    VARCHAR(12)  PRIMARY KEY,
    name          VARCHAR(160) NOT NULL,
    customer_id   VARCHAR(12)  NOT NULL REFERENCES customers(customer_id),
    manager_id    VARCHAR(12)  NOT NULL REFERENCES employees(employee_id),
    contract_ref  VARCHAR(24),
    status        VARCHAR(20)  NOT NULL CHECK (status IN ('proposal','active','closed')),
    start_date    DATE,
    end_date      DATE
);

CREATE TABLE invoices (
    invoice_no    VARCHAR(20)   PRIMARY KEY,
    customer_id   VARCHAR(12)   NOT NULL REFERENCES customers(customer_id),
    project_id    VARCHAR(12)   NOT NULL REFERENCES projects(project_id),
    issued_on     DATE          NOT NULL,
    due_on        DATE          NOT NULL,
    net_gbp       NUMERIC(12,2) NOT NULL,
    vat_gbp       NUMERIC(12,2) NOT NULL,
    total_gbp     NUMERIC(12,2) NOT NULL,
    status        VARCHAR(12)   NOT NULL CHECK (status IN ('open','paid','void')),
    CHECK (total_gbp = net_gbp + vat_gbp)
);

CREATE TABLE contact_notes (
    note_id       SERIAL       PRIMARY KEY,
    contact_id    INTEGER      NOT NULL REFERENCES contacts(contact_id),
    written_by    VARCHAR(12)  NOT NULL REFERENCES employees(employee_id),
    written_on    DATE         NOT NULL,
    note          TEXT         NOT NULL
);

INSERT INTO employees (employee_id, full_name, email, title, office) VALUES
  ('LA-001', 'Mira Oyelaran',         'mira.oyelaran@larkspur-analytics.example',         'Chief Executive',          'Bristol'),
  ('LA-007', 'Tobias Reinholt',       'tobias.reinholt@larkspur-analytics.example',       'Head of Delivery',         'Bristol'),
  ('LA-012', 'Ines Marchetti-Holm',   'ines.marchetti-holm@larkspur-analytics.example',   'Lead Data Scientist',      'Bristol'),
  ('LA-015', 'Callum Driscoll-Park',  'callum.driscoll-park@larkspur-analytics.example',  'Finance Manager',          'Bristol'),
  ('LA-019', 'Ada Nwachukwu-Barr',    'ada.nwachukwu-barr@larkspur-analytics.example',    'People & Culture Lead',    'Bristol');

INSERT INTO customers (customer_id, name, domain, sector, city, account_owner, created_at) VALUES
  ('C-1001', 'Pellucid Freightways Ltd',         'pellucid-freightways.example', 'Logistics', 'Southampton', 'LA-007', '2026-05-12'),
  ('C-1002', 'Corriedale Grocers Co-operative',  'corriedale-grocers.example',   'Retail',    'Leeds',       'LA-012', '2026-06-03'),
  ('C-1003', 'Tamsinbay Water Authority',        'tamsinbay-water.example',      'Utilities', 'Tamsinbay',   'LA-001', '2026-09-08');

INSERT INTO contacts (customer_id, full_name, email, phone, role) VALUES
  ('C-1001', 'Rowan Achterberg',     'rowan.achterberg@pellucid-freightways.example',   '+44 20 7946 0321', 'Head of Network Planning'),
  ('C-1001', 'Selin Kaya-Morrow',    'selin.kaya-morrow@pellucid-freightways.example',  '+44 20 7946 0322', 'Head of Procurement'),
  ('C-1002', 'Hamish Oduya',         'hamish.oduya@corriedale-grocers.example',         '+44 20 7946 0455', 'Supply Chain Director'),
  ('C-1002', 'Petra Lindqvist-Shaw', 'petra.lindqvist-shaw@corriedale-grocers.example', '+44 20 7946 0456', 'Category Analyst'),
  ('C-1003', 'Oluwaseun Pettifer',   'oluwaseun.pettifer@tamsinbay-water.example',      '+44 20 7946 0590', 'Asset Data Manager');

INSERT INTO projects (project_id, name, customer_id, manager_id, contract_ref, status, start_date, end_date) VALUES
  ('PRJ-101', 'Route Optimisation Pilot',    'C-1001', 'LA-007', 'MSA-2026-014', 'active',   '2026-07-01', '2026-12-18'),
  ('PRJ-102', 'Demand Forecasting Rollout',  'C-1002', 'LA-012', 'SOW-2026-021', 'active',   '2026-08-01', '2027-01-29'),
  ('PRJ-103', 'Leak Detection Discovery',    'C-1003', 'LA-001', NULL,           'proposal', NULL,         NULL);

INSERT INTO invoices (invoice_no, customer_id, project_id, issued_on, due_on, net_gbp, vat_gbp, total_gbp, status) VALUES
  ('INV-2026-0041', 'C-1001', 'PRJ-101', '2026-08-31', '2026-09-30', 18500.00, 3700.00, 22200.00, 'paid'),
  ('INV-2026-0057', 'C-1002', 'PRJ-102', '2026-09-15', '2026-10-15', 12750.00, 2550.00, 15300.00, 'open'),
  ('INV-2026-0063', 'C-1001', 'PRJ-101', '2026-09-30', '2026-10-30',  9250.00, 1850.00, 11100.00, 'open');

INSERT INTO contact_notes (contact_id, written_by, written_on, note) VALUES
  (1, 'LA-007', '2026-07-14', 'Rowan Achterberg is the day-to-day sponsor for PRJ-101; prefers weekly written updates.'),
  (3, 'LA-012', '2026-08-19', 'Hamish Oduya agreed Leeds-first rollout at the steering meeting; Harrogate after backtest.'),
  (5, 'LA-001', '2026-09-08', 'Oluwaseun Pettifer asked for a discovery proposal on leak detection for November.');

-- A read-only role for the MCP server (restricted access mode still gets least privilege).
CREATE ROLE walnut_reader LOGIN PASSWORD 'walnut_reader_synthetic';
GRANT CONNECT ON DATABASE larkspur TO walnut_reader;
GRANT USAGE ON SCHEMA public TO walnut_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO walnut_reader;
