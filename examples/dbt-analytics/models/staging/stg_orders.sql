select
  cast(order_id as integer) as order_id,
  cast(order_date as date) as order_date,
  cast(recognized_revenue as decimal(18, 2)) as recognized_revenue,
  customer_id
from {{ source('raw', 'orders') }}
