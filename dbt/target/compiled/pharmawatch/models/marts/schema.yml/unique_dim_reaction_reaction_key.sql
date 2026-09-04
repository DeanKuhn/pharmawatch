
    
    

select
    reaction_key as unique_field,
    count(*) as n_records

from "pharmawatch_dev"."main"."dim_reaction"
where reaction_key is not null
group by reaction_key
having count(*) > 1


