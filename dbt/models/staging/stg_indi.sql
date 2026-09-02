with deduped as (

	select * from {{ source('faers', 'indi') }}

),

indi as (
	
	select
		primaryid,
		caseid,
		try_cast(indi_drug_seq as integer) as indi_drug_seq,
		indi_pt as indication_pt

	from deduped

)

select * from indi
