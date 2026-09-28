with cleansed as (

  select
    drugname,
    prod_ai,
    role_cod,

    -- parenthesis
    regexp_replace(drugname, '\s*\([^)]*\)', '', 'g') as parens_out,

    -- trailing punctuation
    regexp_replace(trim(regexp_replace(parens_out, '[.,;:\s]+$', '')), '\s+', ' ', 'g') as punct_out,

    -- form word at end
    regexp_replace(punct_out, '\s+(TABLETS?|CAPSULES?|CAPLETS?|SOLUTION FOR INJECTION|INJECTION)$', '') 
      as form_out,

    -- trailing strength
    regexp_replace(form_out, '\s+[0-9][0-9./]*\s*(MG|MCG|G|ML|%)$', '') as stren_out,

    nullif(stren_out, '') as drugname_clean

  from main.stg_drug

),

known_pairs as (

  select
    drugname_clean,
    prod_ai,
    count(*) as n

  from cleansed

  where prod_ai is not null

  group by drugname_clean, prod_ai

),

target as (

  select
    drugname,
    drugname_clean,
    count(*) as n --,
    -- punct_out,
    -- parens_out

  from cleansed

  where prod_ai is null
  and role_cod = 'PS'

  group by drugname, drugname_clean --, parens_out, punct_out

),

lookup as (

  select
    drugname_clean,
    arg_max(prod_ai, n) as modal_ai,
    sum(n) as support,
    max(n) / sum(n) as purity,
    count(distinct prod_ai) as n_ai

  from known_pairs

  group by drugname_clean

),

joined as (

  select
    t.drugname,
    t.drugname_clean,
    t.n,
    l.modal_ai,
    l.support,
    l.purity,
    l.n_ai,
    -- t.punct_out,
    -- t.parens_out,

    case
      when l.drugname_clean is null then 'no_match'
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
  left join lookup l on t.drugname_clean = l.drugname_clean

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

-- spot_check as (
--
--   select 
--     drugname, 
--     drugname_clean,
--     modal_ai,
--     purity,
--     n_ai
--
--   from joined
--
--   where parens_out <> punct_out
--   and band in ('mostly', 'ambiguous')
--
--   order by n desc
--
--   limit 30
--
-- )
--
-- select * from spot_check

-- tail as (
--
--   select 
--     stratum,
--     case
--       when regexp_matches(drugname_clean, 
--         '\s(TABLETS?|CAPSULES?|CAPLETS?|SOLUTION FOR INJECTION|INJECTION)$')
--           then 'form_word'
--       when regexp_matches(drugname_clean, 
--         '\s(TAB|CAP|INJ|PFS|NEB|ER TAB|DR)$')
--           then 'short_form'
--     else 'none'
--     end as tail,
--     count(*) as names,
--     sum(n) as n_rows
--
--   from joined
--
--   where tail <> 'none'
--
--   group by stratum, tail
--
-- )
--
-- select * from tail


-- final as (
--
--   select
--     count(distinct drugname) filter (where role_cod = 'PS') as old_ps,
--     count(distinct drugname_clean) filter (where role_cod = 'PS') as new_ps,
--     count(distinct drugname) filter (where role_cod = 'PS' and prod_ai is null) as old_null_ai,
--     count(distinct drugname_clean) filter (where role_cod = 'PS' and prod_ai is null) as new_null_ai
--
--   from cleansed
--
-- )
--
-- select * from final
