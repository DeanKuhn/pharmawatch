with counts as (

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

)

select
  piece_trim,
  sum(n_rows) as sum

from dedup 

group by piece_trim

order by sum(n_rows) desc
limit 20


-- repeated_pieces as (
--
--   select
--     *
--
--   from pieces_per_prodai
--
--   where distinct_piece_trim < n_pieces
--
-- )
--
-- select * from repeated_pieces order by n_pieces desc limit 20

-- distribution as (
--
--   select
--     n_pieces,
--     count(n_pieces) as n_prod_ai,
--     sum(n_rows) as n_rows
--
--   from pieces_per_prodai
--
--   group by n_pieces
--
-- )
--
-- select * from distribution order by n_pieces
