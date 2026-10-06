-- with parentheticals as (
--
--   select
--     drugname as raw,
--     regexp_matches(drugname, '[()]') as has_paren,
--     regexp_matches(drugname, '\([^)]*\(') as nested,
--     regexp_matches(drugname, '^[^(]*\)') as close_b4_open,
--     replace(drugname, '(', '') as open_removed,
--     replace(drugname, ')', '') as closed_removed,
--     len(open_removed) <> len(closed_removed) as unbalanced
--
--   from main.stg_drug
--
-- ),
--
-- groups as (
--
--   select
--     count(*) as total,
--     count(*) filter (where has_paren) as paren_count,
--     count(*) filter (where nested or close_b4_open or unbalanced) as horror_count
--
--   from parentheticals
--
-- ),
--
-- final as (
--
--   select
--     total,
--     paren_count,
--     horror_count,
--     round(100 * (paren_count / total), 2) as pct_paren_total,
--     round(100 * (horror_count / paren_count), 2) as pct_horror_paren,
--     round(100 * (horror_count / total), 2) as pct_horror_total
--
--   from groups
--
-- ),

with paren_parse as (

  select
    drugname as raw,
    upper(trim(unnest(regexp_extract_all(drugname, '\(([^()]*)\)', 1)))) 
      as parsed_parens

  from main.stg_drug

  where role_cod = 'PS'

),

groups as (

  select
    parsed_parens,
    count(*) as n_rows,
    count(distinct raw),
    any_value(raw)

  from paren_parse

  group by parsed_parens

)

select count(raw) from paren_parse
where regexp_matches(parsed_parens,
  '\b(RABBIT|EQUINE|HORSE|LAPINE|PORCINE|BOVINE|I[- ]?131|LU[- ]?177|TC[- ]?99M|GA[- ]?68)\b')
