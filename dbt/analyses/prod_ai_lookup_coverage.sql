-- Answers two questions:
-- 1. What share of unfilled PS rows have a drugname that appears somewhere with a filled prod_ai?
-- 2. For those names, how consistently does the name map to a simple prod_ai?

with known_pairs as (

  select
    drugname,
    prod_ai,
    count(*) as n

  from main.stg_drug

  where prod_ai is not null

  group by drugname, prod_ai

),

target as (

  select
    drugname,
    role_cod,
    count(*) as n

  from main.stg_drug

  where prod_ai is null
    and role_cod = 'PS'

  group by drugname, role_cod

),

lookup as (

  select
    drugname,
    arg_max(prod_ai, n) as modal_ai,
    sum(n) as support,
    max(n) / sum(n) as purity,
    count(distinct prod_ai) as n_ai

  from known_pairs

  group by drugname

),

joined as (

  select
    t.drugname,
    t.n,
    l.modal_ai,
    l.support,
    l.purity,
    l.n_ai,

    case
      when l.drugname is null then 'no_match'
      when l.support < 5 then 'low_support'
      when l.purity >= 0.95 then 'pure'
      when l.purity >= 0.8 then 'mostly'
      else 'ambiguous'
      end as band,

    case
      when t.n >= 1000 then 'head'
      when t.n >= 40 then 'upper-mid'
      when t.n >= 3 then 'mid'
      else 'tail'
      end as stratum

  from target t
  left join lookup l on t.drugname = l.drugname

),

final as (

  select
    stratum,
    band,
    count(*) as names,
    sum(n) as n_rows,
    round(100.0 * sum(n) / sum(sum(n)) over (), 1) as pct_rows

  from joined

  group by stratum, band

)

select * from final order by stratum, band

-- select drugname, prod_ai, n
-- from known_pairs
-- where drugname in ('TRACLEER', 'PROGRAF', 'LEVITRA', 'CELLCEPT',
--                    'ENABLEX', 'ZOLPIDEM', 'DICLOFENAC SODIUM', 'GEMCITABINE')
-- order by drugname, n desc
