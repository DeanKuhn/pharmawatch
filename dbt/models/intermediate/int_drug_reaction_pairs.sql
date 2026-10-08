{{ config(materialized='table') }}
-- IMPORTANT: separate doses per drug will be merged into one via group by

with drug_reaction_pairs as (

	select
		d.primaryid,
    i.identity_key,
    r.reaction_pt

  from {{ ref('int_drug_resolved') }} d
  left join {{ ref('int_prod_ai_identity') }} i using (prod_ai_resolved)
  inner join {{ ref('stg_reac') }} r on d.primaryid = r.primaryid

  where d.role_cod = 'PS'

  group by d.primaryid, i.identity_key, r.reaction_pt 

)

select * from drug_reaction_pairs
