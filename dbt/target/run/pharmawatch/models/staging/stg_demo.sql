
  
  create view "pharmawatch_dev"."main"."stg_demo__dbt_tmp" as (
    with deduped as (

	select * from '../data/deduped/demo.parquet'

),

demo as (

	select
		primaryid,
		caseid,
		coalesce(try_cast(caseversion as integer), 0) as caseversion,
		i_f_code,
		
case
    when length(event_dt) = 8 then try_cast(event_dt as date)
    when length(event_dt) = 6 then try_cast(event_dt || '01' as date)
    when length(event_dt) = 4 then try_cast(event_dt || '0101' as date)
    else null
end
 as event_dt,
		
case
    when length(fda_dt) = 8 then try_cast(fda_dt as date)
    when length(fda_dt) = 6 then try_cast(fda_dt || '01' as date)
    when length(fda_dt) = 4 then try_cast(fda_dt || '0101' as date)
    else null
end
 as fda_dt,
		
case
    when length(init_fda_dt) = 8 then try_cast(init_fda_dt as date)
    when length(init_fda_dt) = 6 then try_cast(init_fda_dt || '01' as date)
    when length(init_fda_dt) = 4 then try_cast(init_fda_dt || '0101' as date)
    else null
end
 as init_fda_dt,
		
case
    when length(death_dt) = 8 then try_cast(death_dt as date)
    when length(death_dt) = 6 then try_cast(death_dt || '01' as date)
    when length(death_dt) = 4 then try_cast(death_dt || '0101' as date)
    else null
end
 as death_dt,
		try_cast(age as decimal) as age,
		age_cod,
		age_grp,
		sex,
		try_cast(wt as decimal) as wt,
		wt_cod,
		rept_cod,
		occp_cod,
		reporter_country,
		occr_country,
		mfr_sndr

	from deduped

)

select * from demo
  );
