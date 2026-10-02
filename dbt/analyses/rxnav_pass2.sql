with pairs as (

  select
    piece,
    unnest(response.idGroup.rxnormId) as rxcui

  from read_json('data/json/rxnav/rxcui/*.json')

),

props as (

  select
    rxcui,
    response.properties.tty as tty,
    response.properties.name as name,

  from read_json('data/json/rxnav/properties/*.json')

),

-- related as (
--
--   select
--     response.relatedGroup.conceptGroup.tty as tty,
--     count(response.relatedGroup.conceptGroup.tty) as n_in,
--
--   from read_json('data/json/rxnav/related/*.json')
--
--   group by response.relatedGroup.conceptGroup.tty
--
-- ),

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

joined as (

  select distinct
    pa.piece,
    pa.rxcui,
    pr.tty,
    pr.name,
    case
      when pr.tty in ('IN', 'PIN', 'IN/PIN') then 'ingredient'
      when pr.tty = 'MIN' then 'multi'
      when pr.tty = 'BN' then 'brand'
      when pr.tty in ('SCD', 'SBD') then 'product'
      else 'other'
    end as tty_class,
    we.n_rows

  from pairs pa
  join props pr on pa.rxcui = pr.rxcui
  join weights we on we.piece = pa.piece
  
),

dedup_2 as (

  select distinct
    tty_class,
    piece,
    n_rows

  from joined

),

final as (

  select
    tty_class,
    count(tty_class) as n_pieces,
    sum(n_rows) as n_rows

  from dedup_2

  group by tty_class

)


-- final as (
--
--   select 
--     piece, rxcui, tty, name, tty_class, n_rows
--
--   from joined
--
--   where tty_class <> 'ingredient'
--
--   order by tty_class, n_rows desc
--
-- )

select * from final
