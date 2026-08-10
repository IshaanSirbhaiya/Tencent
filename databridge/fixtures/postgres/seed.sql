-- DataBridge fintech fixture DB (02_TRD Task 3). Mirrors the static SchemaExtract fixture
-- (packages/ingest/.../fintech_schema_extract.json) so E1 against the LIVE DB matches.
--
-- FK design: transactions→users, transactions→merchants, disputes→transactions are
-- DECLARED. support_messages.user_id references users.id but is the ONE DELIBERATELY
-- UNDECLARED FK — the heuristic FK finder (confidence<1.0) must catch it.

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    email         VARCHAR(255) NOT NULL,
    full_name     VARCHAR(255) NOT NULL,
    created_at    TIMESTAMP    NOT NULL,
    is_active     BOOLEAN      NOT NULL,
    country       VARCHAR(8),
    signup_source VARCHAR(32),
    risk_score    NUMERIC(6,2)
);

CREATE TABLE merchants (
    id          INTEGER PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    category    VARCHAR(64)  NOT NULL,
    created_at  TIMESTAMP    NOT NULL,
    is_verified BOOLEAN      NOT NULL,
    website     VARCHAR(255),
    country     VARCHAR(8)
);

CREATE TABLE transactions (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),       -- DECLARED FK
    merchant_id INTEGER NOT NULL REFERENCES merchants(id),   -- DECLARED FK
    amount      NUMERIC(12,2) NOT NULL,
    currency    VARCHAR(8)    NOT NULL,
    status      VARCHAR(16)   NOT NULL,
    created_at  TIMESTAMP     NOT NULL,
    description TEXT
);

CREATE TABLE disputes (
    id              INTEGER PRIMARY KEY,
    transaction_id  INTEGER NOT NULL REFERENCES transactions(id),  -- DECLARED FK
    reason          VARCHAR(32) NOT NULL,
    status          VARCHAR(16) NOT NULL,
    opened_at       TIMESTAMP   NOT NULL,
    resolved        BOOLEAN     NOT NULL,
    amount_disputed NUMERIC(12,2)
);

CREATE TABLE support_messages (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL,    -- ★ UNDECLARED FK → users(id) (no REFERENCES clause)
    body          TEXT    NOT NULL,
    sent_at       TIMESTAMP NOT NULL,
    is_from_agent BOOLEAN NOT NULL,
    channel       VARCHAR(16) NOT NULL
);

INSERT INTO users (id, email, full_name, created_at, is_active, country, signup_source, risk_score) VALUES
  (1,'ana@example.com','Ana Reyes','2025-01-04T09:12:00',TRUE,'PH','web',12.5),
  (2,'ben@example.com','Ben Cruz','2025-02-18T14:03:00',FALSE,'SG','referral',47.0),
  (3,'cara@example.com','Cara Lim','2025-03-22T08:45:00',TRUE,'US','ios',3.2);

INSERT INTO merchants (id, name, category, created_at, is_verified, website, country) VALUES
  (1,'Sunrise Coffee','food','2024-11-01T00:00:00',TRUE,'https://sunrise.example','PH'),
  (2,'TechBay','electronics','2024-12-15T00:00:00',FALSE,'https://techbay.example','SG');

INSERT INTO transactions (id, user_id, merchant_id, amount, currency, status, created_at, description) VALUES
  (1001,1,2,129.95,'USD','settled','2025-04-01T12:00:00','Online purchase'),
  (1002,2,1,4.50,'USD','settled','2025-04-01T12:05:00','Coffee'),
  (1003,3,2,1999.00,'USD','pending','2025-04-02T09:30:00','Laptop');

INSERT INTO disputes (id, transaction_id, reason, status, opened_at, resolved, amount_disputed) VALUES
  (5,1001,'fraud','open','2025-04-10T09:00:00',FALSE,129.95),
  (6,1002,'duplicate','resolved','2025-04-11T16:30:00',TRUE,4.50);

INSERT INTO support_messages (id, user_id, body, sent_at, is_from_agent, channel) VALUES
  (90001,1,'Hi, my card was charged twice for one order.','2025-04-12T10:01:00',FALSE,'email'),
  (90002,1,'Thanks for reaching out — we are looking into it.','2025-04-12T10:09:00',TRUE,'email'),
  (90003,2,'Where is my refund?','2025-04-13T11:00:00',FALSE,'chat');
