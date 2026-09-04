with deduped as (

	select * from '../data/deduped/ther.parquet'

),

ther as (

	select
		primaryid,
		caseid,
		try_cast(dsg_drug_seq as integer) as drug_seq,
		
case
    when length(start_dt) = 8 then try_cast(start_dt as date)
    when length(start_dt) = 6 then try_cast(start_dt || '01' as date)
    when length(start_dt) = 4 then try_cast(start_dt || '0101' as date)
    else null
end
 as start_dt,
		
case
    when length(end_dt) = 8 then try_cast(end_dt as date)
    when length(end_dt) = 6 then try_cast(end_dt || '01' as date)
    when length(end_dt) = 4 then try_cast(end_dt || '0101' as date)
    else null
end
 as end_dt,
		try_cast(dur as decimal) as dur,
		dur_cod

	from deduped

)

select * from ther