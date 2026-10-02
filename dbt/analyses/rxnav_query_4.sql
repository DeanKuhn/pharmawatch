with pairs as (

  select
    piece,
    unnest(response.idGroup.rxnormId) as rxcui

  from read_json('data/json/rxnav/rxcui/*.json')

),

props as (

  select
    rxcui,
    response.properties.name as name

  from read_json('data/json/rxnav/properties/*.json')

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

exact_pieces as (

  select
    distinct(pa.piece) as piece

  from pairs pa
  join props pr on pa.rxcui = pr.rxcui and pa.piece = upper(pr.name)

),

normalized as (

  select
    rxcui,
    piece

  from pairs

  where piece not in (select piece from exact_pieces)

),

piece_ins as (

  select
    nm.piece as piece,
    n.in_name as in_name,
    contains(lower(piece), lower(in_name)) as found

  from normalized nm
  join names n on nm.rxcui = n.rxcui

), 

per_piece as (

  select
    p.piece,
    w.n_rows,
    bool_and(found) as all_found,
    list(distinct in_name) as in_names

  from piece_ins p
  join weights w on p.piece = w.piece

  group by p.piece, w.n_rows

),

final as (
  
  select
    count(*) as n_pieces,
    sum(n_rows) as n_rows,
    not all_found as flagged

  from per_piece

  group by flagged

)

select * from final
