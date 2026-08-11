select
  order_id,
  customer_id,
  sku,
  booked_date,
  order_date,
  promised_date,
  delivered_date,
  order_status,
  booked_revenue,
  recognized_revenue,
  ordered_quantity,
  available_inventory,
  work_in_progress,
  delivered_date > promised_date as late_order
from {{ ref('stg_orders') }}
