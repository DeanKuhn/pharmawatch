{{ config(materialized='table') }}

with seed_map as (

  select * from {{ ref('biologic_split_map') }}

),

by_ai as (

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
    n

  from by_ai

),

piece_rows as (

  select
    upper(trim(unnest(string_split(cand_string, '\')))) as piece,
    n

  from cands

),

pieces as (

  select
    piece,
    sum(n) as n_rows

  from piece_rows

  group by piece

),

seed_rule as (

  select
    p.piece,
    s.identity,
    s.identity_source,
    s.tty,
    'matched' as status,
    'seed' as rule,
    1 as rule_rank

  from pieces p
  inner join seed_map s on s.piece = p.piece

),

piece_ins as (

  select distinct
    p.piece,
    rel.in_rxcui,
    rel.in_name

  from pieces p
  join {{ ref('stg_rxnav_rxcui') }} r on r.piece = p.piece
  join {{ ref('stg_rxnav_related') }} rel on rel.rxcui = r.rxcui

),

piece_in_counts as (

  select
    piece,
    count(*) as n_ins

  from piece_ins

  group by piece

),

rxnav_rule as (

  select
    pi.piece,
    'rxcui:' || pi.in_rxcui as identity,
    'rxnorm' as identity_source,
    'IN' as tty,
    'matched' as status,
    'rxnav_in' as rule,
    2 as rule_rank

  from piece_ins pi
  join piece_in_counts c using (piece)
  
  where c.n_ins = 1

),

exact_name as (

  select
    pi.piece,
    pi.in_rxcui

  from piece_ins pi
  join piece_in_counts c using (piece)

  where c.n_ins > 1
    and upper(pi.in_name) = pi.piece

),

name_pick_rule as (

  select
    c.piece,
    case when count(e.in_rxcui) = 1 then 'rxcui:' || any_value(e.in_rxcui) end as identity,
    case when count(e.in_rxcui) = 1 then 'rxnorm' end as identity_source,
    case when count(e.in_rxcui) = 1 then 'IN' end as tty,
    case when count(e.in_rxcui) = 1 then 'matched' else 'ambiguous' end as status,
    'name_pick' as rule,
    3 as rule_rank

  from piece_in_counts c
  left join exact_name e using (piece)

  where c.n_ins > 1

  group by c.piece

),

placeholder_rule as (

  select
    piece,
    null as identity,
    null as identity_source,
    null as tty,
    'non_specific' as status,
    'placeholder' as rule,
    4 as rule_rank

  from pieces

  where piece like '% NOS'
   or piece in ('COSMETICS', 'UNSPECIFIED INGREDIENT', 'DEVICE',
                'INVESTIGATIONAL PRODUCT', 'VITAMINS', 'HERBALS',
                'MINERALS', 'DIETARY SUPPLEMENT', 'AMINO ACIDS',
                'INFLUENZA VIRUS VACCINE')
),

local_rule as (
  
  select
    piece,
    'local:' || piece as identity,
    'local' as identity_source,
    null as tty,
    'unmatched' as status,
    'local' as rule,
    6 as rule_rank

  from pieces

),

candidates as (

  select * from seed_rule
  union all by name 
  select * from rxnav_rule
  union all by name 
  select * from name_pick_rule
  union all by name 
  select * from placeholder_rule
  union all by name 
  select * from local_rule

),

final as (

  select
    c.*,
    p.n_rows

  from candidates c
  join pieces p using (piece)
  qualify c.rule_rank = min(c.rule_rank) over (partition by c.piece)
  
)

select * from final
