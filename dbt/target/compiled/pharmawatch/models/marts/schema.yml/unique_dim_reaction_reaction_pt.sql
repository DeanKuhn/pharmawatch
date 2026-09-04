
    
    

select
    reaction_pt as unique_field,
    count(*) as n_records

from "pharmawatch_dev"."main"."dim_reaction"
where reaction_pt is not null
group by reaction_pt
having count(*) > 1


