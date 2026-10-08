{{ config(materialized='table') }}

with by_ai as (

  select
    prod_ai_resolved,
    count(*) as n

  from {{ ref('int_drug_resolved') }}

  where prod_ai_resolved is not null

  group by prod_ai_resolved

),

cands as (

  select
    upper(trim(unnest(string_split(replace(prod_ai_resolved, ' AND OR ', ' OR '), ' OR ' )))) 
      as cand_string,
    prod_ai_resolved,
    n

  from by_ai

),

piece_rows as (

  select
    upper(trim(unnest(string_split(cand_string, '\')))) as piece,
    prod_ai_resolved,
    n

  from cands

),

joined as (

  select
    r.prod_ai_resolved,
    r.n,
    r.piece,
    i.identity,
    i.status

  from piece_rows r
  join {{ ref('int_piece_ingredients') }} i on r.piece = i.piece

),

rolled as (

  select
    prod_ai_resolved,
    n,
    
    case
      when prod_ai_resolved like '% OR %' then 'ambiguous'
      when bool_or(status = 'ambiguous') then 'ambiguous'
      when bool_or(status = 'non_specific') then 'non_specific'
      when bool_or(status = 'unmatched') then 'unmatched'
      else 'matched'
    end as status,

    list_sort(list_distinct(list(identity))) as raw_set

  from joined

  group by prod_ai_resolved, n

),

final as (

  select
    prod_ai_resolved,
    n as n_rows,
    status,

    case when status in ('matched', 'unmatched') then raw_set end as identity_set,
    array_to_string(identity_set, '|') as identity_key,
    len(identity_set) as n_ingredients

  from rolled

)

select * from final
