select sum(recognized_revenue) as recognized_revenue
from {{ ref('fct_orders') }}
where order_status != 'cancelled'
  and order_date >= '2026-07-01'
  and order_date < '2026-08-01'
