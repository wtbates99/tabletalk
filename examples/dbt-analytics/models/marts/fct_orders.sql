select order_id, order_date, recognized_revenue, customer_id
from {{ ref('stg_orders') }}
