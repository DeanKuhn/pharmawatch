-- A3: exact piece strings for the 011 seed map families.
-- Pieces come from prod_ai_resolved, so both reported and lookup-filled rows count.

with counts as (

  select
    prod_ai_resolved,
    prod_ai_source,
    count(*) as n_rows

  from main.int_drug_resolved

  where prod_ai_resolved is not null

  group by prod_ai_resolved, prod_ai_source

),

exploded as (

  select
    prod_ai_source,
    n_rows,
    upper(trim(unnest(split(prod_ai_resolved, '\')))) as piece

  from counts

),

tagged as (

  select
    *,
    case
      when piece like '%ELASOMERAN%' or piece like '%TOZINAMERAN%'
        or piece like '%MRNA%' or piece like '%CX-0%' or piece like '%BNT%'
        or piece like '%COVID%' or piece like '%SARS%' then 'covid_vax'
      when piece like '%AZD%' or piece like '%CHADOX%' then 'azd1222'
      when piece like '%THYMOCYTE%' or piece like '%LYMPHOCYTE IMMUNE%' then 'atg'
      when piece like '%DOTATATE%' or piece like '%LUTETIUM%' then 'dotatate'
      when piece like '%ESTERASE%' then 'c1_inh'
    end as family

  from exploded

)

select
  family,
  piece,
  sum(n_rows) as n_rows,
  sum(n_rows) filter (where prod_ai_source = 'reported') as n_reported,
  sum(n_rows) filter (where prod_ai_source = 'lookup') as n_lookup

from tagged

where family is not null

group by family, piece

order by family, n_rows desc
