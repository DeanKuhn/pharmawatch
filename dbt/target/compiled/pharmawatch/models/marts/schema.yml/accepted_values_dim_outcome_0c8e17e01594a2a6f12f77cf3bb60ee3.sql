
    
    

with all_values as (

    select
        outcome_code as value_field,
        count(*) as n_records

    from "pharmawatch_dev"."main"."dim_outcome"
    group by outcome_code

)

select *
from all_values
where value_field not in (
    'DE','HO','LT','DS','CA','RI','OT'
)


