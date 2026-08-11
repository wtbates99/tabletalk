select sum(recognized_revenue) as recognized_revenue
from analytics.main.fct_orders
where order_status != 'cancelled'
  and order_date >= '2026-07-01'
  and order_date < '2026-08-01'
