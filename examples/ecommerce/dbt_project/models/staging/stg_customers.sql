select
    id,
    name,
    email,
    city,
    country,
    signup_date,
    active,
    lifetime_value
from {{ source('main', 'customers') }}
