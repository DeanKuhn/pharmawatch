with deduped as (

	select * from {{ source('faers', 'drug') }}

),

drug as (

	select
		primaryid,
		caseid,
		try_cast(drug_seq as integer) as drug_seq,
		role_cod,
    upper(trim(drugname)) as drugname,
    {{ clean_drugname('upper(trim(drugname))') }} as drugname_clean,
		route,
		try_cast(dose_amt as double) as dose_amt,
		dose_unit,
		dose_form,
		dose_freq,
		nullif(upper(trim(prod_ai)), '') as prod_ai,
		nda_num,
		coalesce(lot_num, lot_nbr) as lot_number,
		dechal,
		rechal,
		{{ parse_faers_date('exp_dt') }} as exp_dt

	from deduped

)

select * from drug
