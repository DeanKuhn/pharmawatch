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
    count_if(prod_ai is not null and trim(prod_ai) != '') as filled,
    count_if(prod_ai is not null and prod_ai != '') / count(*) as fill_pct

  from filed

  group by yr
      
)

select * from final order by yr
