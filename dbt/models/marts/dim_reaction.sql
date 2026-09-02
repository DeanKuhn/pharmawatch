-- one row per unique reaction

with dim_reaction as (

	select distinct
		{{ dbt_utils.generate_surrogate_key(['reaction_pt']) }} as reaction_key,
		reaction_pt

	from {{ ref('int_drug_reaction_pairs') }}

)

select * from dim_reaction

