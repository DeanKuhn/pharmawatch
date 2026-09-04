
    
    

select
    drugname as unique_field,
    count(*) as n_records

from "pharmawatch_dev"."main"."dim_drug"
where drugname is not null
group by drugname
having count(*) > 1


