{{ config(materialized='table') }}

with joined as (

  select
    d.primaryid,
    d.caseid,
    d.drug_seq,
    d.role_cod,
    d.drugname,
    d.drugname_clean,
    d.prod_ai,
    l.band,
    l.modal_ai

  from {{ ref('stg_drug') }} d
  left join {{ ref('int_drugname_lookup') }} l
  on d.drugname_clean = l.drugname_clean

),

final as (

  select
    primaryid,
    caseid,
    drug_seq,
    role_cod,
    drugname,
    drugname_clean,
    prod_ai,
    band,
    
    case
      when prod_ai is not null then prod_ai
      when band = 'pure' then modal_ai
    end as prod_ai_resolved,

    case
      when prod_ai is not null then 'reported'
      when band = 'pure' then 'lookup'
    end as prod_ai_source

  from joined

)

select * from final
