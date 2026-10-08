with concept_groups as (

  select
    rxcui,
    unnest(response.relatedGroup.conceptGroup) as concept_g

  from {{ source('rxnav', 'related') }}

),

properties as (

  select
    rxcui,
    unnest(concept_g.conceptProperties) as concept_p

  from concept_groups

),

rxcui as (

  select
    rxcui,
    concept_p.rxcui as in_rxcui,
    concept_p.name as in_name,
    concept_p.tty as in_tty

  from properties

)

select * from rxcui
