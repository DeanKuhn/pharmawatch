with pairs as (

  select
    piece,
    unnest(response.idGroup.rxnormId) as rxcui

  from read_json('data/json/rxnav/rxcui/*.json')

),

related_groups as (

  select
    rxcui as rxcui,
    unnest(response.relatedGroup.conceptGroup) as cg
  
  from read_json('data/json/rxnav/related/*.json')

),

related_in as (

  select
    rxcui, 
    unnest(cg.conceptProperties) as cprop,

  from related_groups

),

names as (

  select
    rxcui,
    cprop.name as in_name,
    cprop.rxcui as in_rxcui

  from related_in

),

piece_ins as (

  select
    p.piece,
    p.rxcui,
    n.in_name,
    n.in_rxcui

  from pairs p
  join names n on p.rxcui = n.rxcui

),

counts as (

  select
    prod_ai,
    count(prod_ai) as n_rows

  from main.stg_drug

  where prod_ai is not null

  group by prod_ai

),

exploded as (
  
  select
    *,
    unnest(split(prod_ai, '\')) as piece_raw

  from counts

),

clean as (

  select
    *,
    upper(trim(piece_raw)) as piece

  from exploded

  where piece <> ''

),

dedup as (

  select
    distinct(prod_ai),
    n_rows,
    piece

  from clean

),

weights as (
  
  select
    piece,
    sum(n_rows) as n_rows

  from dedup

  group by piece

),

per_piece as (

  select
    w.piece as piece,
    count(distinct n.rxcui) as n_ids,
    count(distinct n.in_rxcui) as n_ins,
    list(distinct n.in_name) as in_names,
    w.n_rows as n_rows

  from piece_ins n
  join weights w on n.piece = w.piece

  group by w.piece, n_rows

  having count(distinct n.rxcui) > 1

),

final as (

  select
    count(*) as n_pieces,
    sum(n_rows) as n_rows,
    n_ins = 1 as collapses

  from per_piece

  group by collapses

)

select * from final
