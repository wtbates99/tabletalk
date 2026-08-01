{{ config(enabled=false) }}
select * from {{ ref('raw_orders') }}
