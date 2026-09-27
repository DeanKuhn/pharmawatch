with name_counts as (

  select
    drugname,
    count(*) as n

  from main.stg_drug
  where drugname is not null
    and drugname != ''
    and role_cod == 'PS'

  group by drugname

),

ranked as (

  select
    *,
    row_number() over (order by n desc, drugname) as rk,
    sum(n) over (order by n desc, drugname) as cum_n,
    (sum(n) over (order by n desc, drugname)) / (sum(n) over ()) as cum_pct

  from name_counts

),

out as (

  select * from ranked where rk in (1000, 10000, 50000)

),

out_2 as (

  select
    count(*) as distinct_names,
    count_if(n = 1) as singletons

  from ranked

)

select * from out_2
-- select from either out or out_2
