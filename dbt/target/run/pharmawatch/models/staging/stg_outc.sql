
  
  create view "pharmawatch_dev"."main"."stg_outc__dbt_tmp" as (
    with deduped as (

	select * from '../data/deduped/outc.parquet'

),

outc as (
	
	select
		primaryid,
		caseid,
		coalesce(outc_cod, outc_code) as outcome_code

	from deduped

)

select * from outc
  );
