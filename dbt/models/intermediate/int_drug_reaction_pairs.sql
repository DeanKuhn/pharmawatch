{{ config(materialized='table') }}
-- IMPORTANT: separate doses per drug will be merged into one via group by

with drug_reaction_pairs as (

	select
		d.primaryid,
    i.identity_key,
    r.reaction_pt,

    case when de.occp_cod = 'LW' then true else false end as is_lw,

    cast(date_trunc('quarter', coalesce(init_fda_dt, fda_dt)) as date) as report_quarter

  from {{ ref('int_drug_resolved') }} d
  left join {{ ref('int_prod_ai_identity') }} i using (prod_ai_resolved)
  inner join {{ ref('stg_reac') }} r on d.primaryid = r.primaryid
  left join {{ ref('int_case_demographics') }} de on de.primaryid = d.primaryid

  where d.role_cod = 'PS'

  group by d.primaryid, i.identity_key, r.reaction_pt, is_lw, report_quarter 

)

select * from drug_reaction_pairs
