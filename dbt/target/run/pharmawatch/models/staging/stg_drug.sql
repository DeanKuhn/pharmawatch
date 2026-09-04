
  
  create view "pharmawatch_dev"."main"."stg_drug__dbt_tmp" as (
    with deduped as (

	select * from '../data/deduped/drug.parquet'

),

drug as (

	select
		primaryid,
		caseid,
		try_cast(drug_seq as integer) as drug_seq,
		role_cod,
		drugname,
		route,
		try_cast(dose_amt as decimal) as dose_amt,
		dose_unit,
		dose_form,
		dose_freq,
		prod_ai,
		nda_num,
		coalesce(lot_num, lot_nbr) as lot_number,
		dechal,
		rechal,
		
case
    when length(exp_dt) = 8 then try_cast(exp_dt as date)
    when length(exp_dt) = 6 then try_cast(exp_dt || '01' as date)
    when length(exp_dt) = 4 then try_cast(exp_dt || '0101' as date)
    else null
end
 as exp_dt

	from deduped

)

select * from drug
  );
