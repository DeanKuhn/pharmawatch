with pieces as (

  select
    piece,
    rxnorm_version

  from {{ source('rxnav', 'rxcui') }}

),

matches as (

  select
    piece,
    unnest(response.idGroup.rxnormId) as rxcui

  from {{ source('rxnav', 'rxcui') }}

),

final as (

  select *

  from pieces p
  left join matches m using (piece)

)

select * from final
