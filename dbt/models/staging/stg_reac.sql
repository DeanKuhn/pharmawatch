with deduped as (

	select * from {{source('faers', 'reac') }}

),

reac as (

	select
		primaryid,
		caseid,
		pt as reaction_pt
	
	from deduped

)

select * from reac
