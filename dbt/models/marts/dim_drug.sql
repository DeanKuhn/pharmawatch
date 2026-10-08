with source as (

  select
    prod_ai_resolved,
    identity_key,
    n_rows,
    status,
    n_ingredients

  from {{ ref('int_prod_ai_identity') }}

  where identity_key is not null

),

identity_key_group as (

  select
    identity_key,
    arg_max(prod_ai_resolved, n_rows) as drug_label,
    case when bool_or(status = 'unmatched') then 'unmatched' else 'matched' end
      as status,
    any_value(n_ingredients) as n_ingredients

  from source

  group by identity_key

),

dim_drug as (

  select
    {{ dbt_utils.generate_surrogate_key(['identity_key']) }} as drug_key, *

  from identity_key_group

)

select * from dim_drug
