
    
    

with child as (
    select reaction_key as from_field
    from "pharmawatch_dev"."main"."fct_adverse_events"
    where reaction_key is not null
),

parent as (
    select reaction_key as to_field
    from "pharmawatch_dev"."main"."dim_reaction"
)

select
    from_field

from child
left join parent
    on child.from_field = parent.to_field

where parent.to_field is null


