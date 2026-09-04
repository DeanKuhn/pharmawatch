
    
    

select
    drug_key as unique_field,
    count(*) as n_records

from "pharmawatch_dev"."main"."dim_drug"
where drug_key is not null
group by drug_key
having count(*) > 1


