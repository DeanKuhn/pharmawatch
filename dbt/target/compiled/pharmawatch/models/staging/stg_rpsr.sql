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