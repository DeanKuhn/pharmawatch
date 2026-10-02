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

piece_status as (

  select
    piece,
    case
      when piece in (select piece from exact_pieces) then 'exact'
      when piece in (select piece from pairs) then 'normalized'
      when piece like '% NOS' or piece in (
        'COSMETICS', 'UNSPECIFIED INGREDIENT', 'DEVICE', 
        'INVESTIGATIONAL PRODUCT', 'VITAMINS', 'HERBALS', 
        'MINERALS', 'DIETARY SUPPLEMENT', 'AMINO ACIDS', 
        'INFLUENZA VIRUS VACCINE') then 'non_specific'
      else 'no_match'
    end as status

  from weights

),

ai_status as (
  
  select
    d.prod_ai,
    d.n_rows,

    case
      when d.n_rows >= 1000 then 'head'
      when d.n_rows >= 40 then 'upper-mid'
      when d.n_rows >= 3 then 'mid'
      else 'tail'
    end as stratum,

    case
      when d.prod_ai like '% OR %' then 'ambiguous'
      when bool_or(p.status = 'no_match') then 'no_match'
      when bool_or(p.status = 'non_specific') then 'non_specific'
      when bool_or(p.status = 'normalized') then 'normalized'
      else 'exact'
    end as status

  from dedup d
  join piece_status p on d.piece = p.piece

  group by d.prod_ai, d.n_rows

),

-- final as (
--
--   select
--     stratum,
--     status,
--     count(*) as n_prod_ai,
--     sum(n_rows) as n_rows,
--     round(100 * (sum(n_rows) / sum(sum(n_rows)) over (partition by stratum)), 2) as pct
--
--   from ai_status
--
--   group by stratum, status
--
-- )

final as (

  select *

  from ai_status

  where status = 'no_match'

  order by n_rows desc

  limit 20

)

select * from final
