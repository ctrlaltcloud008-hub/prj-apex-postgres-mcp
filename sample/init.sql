-- Sample schema + data for exercising the pg-mcp server.
-- Runs automatically on first container start (mounted into /docker-entrypoint-initdb.d).
-- Creates a realistic e-commerce schema, seeds data, and provisions the read-only role
-- the MCP connects as.

-- ---------------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------------

CREATE TABLE customers (
    id          serial PRIMARY KEY,
    name        text NOT NULL,
    email       text UNIQUE NOT NULL,
    country     text NOT NULL DEFAULT 'US',
    created_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE customers IS 'People who place orders';
COMMENT ON COLUMN customers.country IS 'ISO country code';
CREATE INDEX idx_customers_country ON customers (country);

CREATE TABLE products (
    id          serial PRIMARY KEY,
    sku         text UNIQUE NOT NULL,
    name        text NOT NULL,
    price_cents integer NOT NULL CHECK (price_cents >= 0),
    tags        text[] NOT NULL DEFAULT '{}',
    attributes  jsonb NOT NULL DEFAULT '{}'
);
COMMENT ON TABLE products IS 'Catalog of purchasable products';
CREATE INDEX idx_products_tags ON products USING gin (tags);

CREATE TABLE orders (
    id          serial PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES customers(id),
    status      text NOT NULL DEFAULT 'pending',
    placed_at   timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE orders IS 'Customer orders (header)';
CREATE INDEX idx_orders_customer ON orders (customer_id);
CREATE INDEX idx_orders_status ON orders (status);

CREATE TABLE order_items (
    id         serial PRIMARY KEY,
    order_id   integer NOT NULL REFERENCES orders(id),
    product_id integer NOT NULL REFERENCES products(id),
    quantity   integer NOT NULL CHECK (quantity > 0),
    unit_cents integer NOT NULL
);
COMMENT ON TABLE order_items IS 'Line items belonging to an order';
CREATE INDEX idx_order_items_order ON order_items (order_id);

CREATE VIEW order_totals AS
SELECT o.id AS order_id,
       o.customer_id,
       o.status,
       sum(oi.quantity * oi.unit_cents) AS total_cents
FROM orders o
JOIN order_items oi ON oi.order_id = o.id
GROUP BY o.id, o.customer_id, o.status;
COMMENT ON VIEW order_totals IS 'Order value rolled up from line items';

-- ---------------------------------------------------------------------------
-- Data
-- ---------------------------------------------------------------------------

INSERT INTO customers (name, email, country) VALUES
    ('Alice Chen',   'alice@example.com',  'US'),
    ('Bob Ndlovu',   'bob@example.com',    'ZA'),
    ('Carla Rossi',  'carla@example.com',  'IT'),
    ('Deepak Rao',   'deepak@example.com', 'IN'),
    ('Emma Sørensen','emma@example.com',   'DK');

INSERT INTO products (sku, name, price_cents, tags, attributes) VALUES
    ('SKU-001', 'Mechanical Keyboard', 8900,  ARRAY['electronics','office'], '{"switch":"brown","backlit":true}'),
    ('SKU-002', 'USB-C Cable',          1200,  ARRAY['electronics','cable'],  '{"length_m":2}'),
    ('SKU-003', 'Standing Desk',        24900, ARRAY['furniture','office'],   '{"width_cm":140}'),
    ('SKU-004', 'Ergonomic Chair',      19900, ARRAY['furniture'],            '{"color":"black"}'),
    ('SKU-005', 'Noise-Cancel Headset', 15900, ARRAY['electronics','audio'],  '{"wireless":true}');

-- ~200 orders spread across customers, with 1-4 line items each.
INSERT INTO orders (customer_id, status, placed_at)
SELECT (1 + (g % 5)),
       (ARRAY['pending','paid','shipped','cancelled'])[1 + (g % 4)],
       now() - (g || ' hours')::interval
FROM generate_series(1, 200) g;

INSERT INTO order_items (order_id, product_id, quantity, unit_cents)
SELECT o.id,
       1 + ((o.id + n) % 5),
       1 + ((o.id + n) % 4),
       p.price_cents
FROM orders o
CROSS JOIN generate_series(0, 2) n
JOIN products p ON p.id = 1 + ((o.id + n) % 5)
WHERE (o.id + n) % 3 <> 0;   -- vary line-item counts

ANALYZE;

-- ---------------------------------------------------------------------------
-- Read-only role the MCP connects as (see ADR 0002 / README).
-- ---------------------------------------------------------------------------

CREATE ROLE mcp_readonly LOGIN PASSWORD 'readonly_dev_pw';
GRANT CONNECT ON DATABASE appdb TO mcp_readonly;
GRANT USAGE ON SCHEMA public TO mcp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_readonly;
