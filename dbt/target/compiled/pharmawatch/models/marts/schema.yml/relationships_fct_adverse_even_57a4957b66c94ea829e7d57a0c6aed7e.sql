
    
    

with child as (
    select demographics_key as from_field
    from "pharmawatch_dev"."main"."fct_adverse_events"
    where demographics_key is not null
),

parent as (
    select demographics_key as to_field
    from "pharmawatch_dev"."main"."dim_demographics"
)

select
    from_field

from child
left join parent
    on child.from_field = parent.to_field

where parent.to_field is null


