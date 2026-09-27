with deduped as (

	select * from {{source('faers', 'reac') }}

),

reac as (

	select
		primaryid,
		caseid,
		upper(trim(pt)) as reaction_pt
	
	from deduped

)

select * from reac
