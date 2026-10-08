-- Every prod_ai_resolved must survive the inner join to int_piece_ingredients,
-- which proves this model splits strings exactly as the piece model does.

select prod_ai_resolved

from {{ ref('int_drug_resolved') }}

where prod_ai_resolved is not null

except

select prod_ai_resolved

from {{ ref('int_prod_ai_identity') }}
