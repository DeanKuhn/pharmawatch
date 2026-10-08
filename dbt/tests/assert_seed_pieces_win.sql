-- Every seed piece must resolve through the seed rule, so a seeded PIN is
-- never rolled up to its IN by a lower-precedence rule (001 amendment, 011).

select
  s.piece,
  i.rule

from {{ ref('biologic_split_map') }} s
left join {{ ref('int_piece_ingredients') }} i on i.piece = s.piece

where i.rule is distinct from 'seed'
