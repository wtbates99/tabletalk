{{ config(alias='fct_orders', schema='other') }}
select order_id, order_date from {{ ref('stg_orders') }}
