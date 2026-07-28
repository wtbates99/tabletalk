CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  order_date DATE NOT NULL,
  status TEXT NOT NULL,
  recognized_revenue NUMERIC NOT NULL
);
