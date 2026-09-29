with landed as (

  select
    piece,
    response.idGroup.rxnormId as ids

  from read_json('data/json/rxnav/rxcui/*.json')

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
    upper(trim(piece_raw)) as piece_trim

  from exploded

  where piece_trim <> ''

),

pieces_per_prodai as (

  select
    prod_ai,
    n_rows,
    count(distinct piece_trim) as n_distinct_pieces,
    count(prod_ai) as n_pieces

  from clean

  group by prod_ai, n_rows

),

dedup as (

  select
    distinct(prod_ai),
    n_rows,
    piece_trim

  from clean

),

weights as (
  
  select
    piece_trim,
    sum(n_rows) as n_rows

  from dedup

  group by piece_trim

),

joined as (

  select
    l.piece,
    w.n_rows,
    l.ids,
    case
      when ids is null then '0'
      when len(ids) = 1 then '1'
      else '2+'
    end as n_ids

  from landed l
  join weights w on l.piece = w.piece_trim

)

select
  piece,
  ids,
  n_rows

from joined

where n_ids = '0'

order by n_rows desc limit 30
