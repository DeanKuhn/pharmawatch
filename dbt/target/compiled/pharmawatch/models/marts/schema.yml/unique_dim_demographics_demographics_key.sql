
    
    

select
    demographics_key as unique_field,
    count(*) as n_records

from "pharmawatch_dev"."main"."dim_demographics"
where demographics_key is not null
group by demographics_key
having count(*) > 1


