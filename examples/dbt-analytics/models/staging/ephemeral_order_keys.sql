{{ config(materialized='ephemeral') }}
select order_id from {{ ref('raw_orders') }}
