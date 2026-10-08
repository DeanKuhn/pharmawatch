with pairs as (

  select
    primaryid,
    identity_key,
    reaction_pt,
    is_lw,
    report_quarter

  from {{ ref('int_drug_reaction_pairs') }}

  where identity_key is not null

),

final as (

  select
    identity_key,
    reaction_pt,
    report_quarter,
    count(distinct primaryid) as n_cases,
    count(distinct primaryid) filter (where not is_lw) as n_cases_nolw

  from pairs

  group by identity_key, reaction_pt, report_quarter

)

select * from final
