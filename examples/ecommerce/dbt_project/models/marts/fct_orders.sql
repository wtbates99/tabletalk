select
    o.id                                        as order_id,
    o.customer_id,
    c.name                                      as customer_name,
    c.city,
    o.status,
    o.total_amount,
    o.discount_amount,
    o.total_amount - o.discount_amount          as net_revenue,
    count(oi.id)                                as item_count,
    o.created_at
from {{ ref('stg_orders') }} o
left join {{ source('main', 'customers') }} c on c.id = o.customer_id
left join {{ source('main', 'order_items') }} oi on oi.order_id = o.id
group by 1, 2, 3, 4, 5, 6, 7, 8, 10
