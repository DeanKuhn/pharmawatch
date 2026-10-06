{{ config(materialized='table') }}

with source as (

  select
    drugname_clean,
    prod_ai

  from {{ ref('stg_drug') }}

  where prod_ai is not null
    and drugname_clean is not null

),

known_pairs as (

  select
    drugname_clean,
    prod_ai,
    count(*) as n

  from source

  group by drugname_clean, prod_ai

),

lookup as (

  select
    drugname_clean,
    arg_max(prod_ai, n) as modal_ai,
    sum(n) as support,

    case
      when sum(n) < 5 then 'low_support'
      when max(n) / sum(n) >= 0.95 then 'pure'
      when max(n) / sum(n) >= 0.8 then 'mostly'
      else 'ambiguous'
    end as band,

    max(n) / sum(n) as purity,
    count(distinct prod_ai) as n_ai

  from known_pairs

  group by drugname_clean

)

select * from lookup
