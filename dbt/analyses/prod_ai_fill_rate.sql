with filed as (

  select
    year(m.fda_dt) as yr,
    d.prod_ai

  from main.stg_drug d
  
  join main.stg_demo m on d.primaryid = m.primaryid

  where d.role_cod = 'PS'

),

final as (

  select
    yr,
    count(*) as total_rows,
    count(prod_ai) as fill_count,
    round(100.0 * count(prod_ai) / count(*), 1) as fill_pct

  from filed

  group by yr
      
)

select * from final order by yr
