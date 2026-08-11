{{ config(alias='fct_orders', schema='other') }}
select order_id, booked_date, order_date from {{ ref('stg_orders') }}
