with deduped as (

	select * from {{ source('faers', 'drug') }}

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
		{{ parse_faers_date('exp_dt') }} as exp_dt

	from deduped

)

select * from drug
