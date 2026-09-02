-- one row per unique drugname, from int_drug_reaction_pairs

with dim_drug as (

	select distinct
		{{ dbt_utils.generate_surrogate_key(['drugname']) }} as drug_key,
		drugname
	
	from {{ ref('int_drug_reaction_pairs') }}

)

select * from dim_drug
