with deduped as (

	select * from {{ source('faers', 'ther') }}

),

ther as (

	select
		primaryid,
		caseid,
		try_cast(dsg_drug_seq as integer) as drug_seq,
		{{ parse_faers_date('start_dt') }} as start_dt,
		{{ parse_faers_date('end_dt') }} as end_dt,
		try_cast(dur as decimal) as dur,
		dur_cod

	from deduped

)

select * from ther
