with deduped as (

	select * from '../data/deduped/reac.parquet'

),

reac as (

	select
		primaryid,
		caseid,
		pt as reaction_pt
	
	from deduped

)

select * from reac