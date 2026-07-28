INSERT INTO analytics.orders VALUES
  (1, '2026-01-05', 'complete'),
  (2, '2026-01-06', 'complete'),
  (3, '2026-01-07', 'cancelled');

INSERT INTO analytics.products VALUES
  (10, 'Footwear'),
  (20, 'Accessories');

INSERT INTO analytics.order_items VALUES
  (100, 1, 10, 80.00),
  (101, 1, 20, 20.00),
  (102, 2, 10, 50.00),
  (103, 3, 10, 900.00);
