with target as (

  select
    drugname,
    count(*) as n,
    coalesce(band, 'no_match') as band

  from main.int_drug_resolved

  where role_cod = 'PS' and prod_ai is null

  group by drugname, band

),

final as (

  select

    case
      when n >= 1000 then 'head'
      when n >= 40 then 'upper-mid'
      when n >= 3 then 'mid'
      else 'tail'
    end as stratum,

    band,
    count(*) as names,
    sum(n) as n_rows,
    round((sum(n) * 100) / sum(sum(n)) over (), 2) as pct_rows

  from target

  group by stratum, band

)

select * from final order by stratum
