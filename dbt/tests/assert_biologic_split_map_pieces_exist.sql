-- Every seed piece must appear in the data, so a typo in the map fails loudly
-- instead of silently never matching. Returns orphan pieces.

with pieces as (

  select distinct
    upper(trim(unnest(split(prod_ai_resolved, '\')))) as piece

  from (
    select distinct prod_ai_resolved
    from {{ ref('int_drug_resolved') }}
    where prod_ai_resolved is not null
  )

)

select s.piece

from {{ ref('biologic_split_map') }} s
left join pieces p on p.piece = s.piece

where p.piece is null
