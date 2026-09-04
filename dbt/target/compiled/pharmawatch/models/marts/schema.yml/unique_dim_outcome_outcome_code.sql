
    
    

select
    outcome_code as unique_field,
    count(*) as n_records

from "pharmawatch_dev"."main"."dim_outcome"
where outcome_code is not null
group by outcome_code
having count(*) > 1


