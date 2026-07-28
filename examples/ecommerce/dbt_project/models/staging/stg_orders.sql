select
    id,
    customer_id,
    status,
    total_amount,
    discount_amount,
    created_at,
    shipped_at,
    delivered_at
from {{ source('main', 'orders') }}
