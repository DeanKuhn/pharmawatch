with deduped as (

	select * from {{ source('faers', 'outc') }}

),

outc as (
	
	select
		primaryid,
		caseid,
		coalesce(outc_cod, outc_code) as outcome_code

	from deduped

)

select * from outc
