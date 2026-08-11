select
  cast(order_id as integer) as order_id,
  customer_id,
  sku,
  cast(booked_date as date) as booked_date,
  cast(order_date as date) as order_date,
  cast(promised_date as date) as promised_date,
  cast(delivered_date as date) as delivered_date,
  order_status,
  cast(booked_revenue as decimal(18, 2)) as booked_revenue,
  cast(recognized_revenue as decimal(18, 2)) as recognized_revenue,
  cast(ordered_quantity as integer) as ordered_quantity,
  cast(available_inventory as integer) as available_inventory,
  cast(work_in_progress as integer) as work_in_progress
from {{ source('raw', 'orders') }}
