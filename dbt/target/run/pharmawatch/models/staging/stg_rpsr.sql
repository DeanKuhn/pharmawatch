
  
  create view "pharmawatch_dev"."main"."stg_rpsr__dbt_tmp" as (
    with deduped as (

	select * from '../data/deduped/rpsr.parquet'

),

rpsr as (

	select
		primaryid,
		caseid,
		rpsr_cod as report_source_code

	from deduped

)

select * from rpsr
  );
