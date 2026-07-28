CREATE SCHEMA analytics;

CREATE TABLE analytics.orders (
  id BIGINT PRIMARY KEY,
  order_date DATE NOT NULL,
  status VARCHAR NOT NULL
);

CREATE TABLE analytics.products (
  id BIGINT PRIMARY KEY,
  category VARCHAR NOT NULL
);

CREATE TABLE analytics.order_items (
  id BIGINT PRIMARY KEY,
  order_id BIGINT NOT NULL,
  product_id BIGINT NOT NULL,
  recognized_revenue DECIMAL(18, 2) NOT NULL
);
