
    
    

select
    primaryid as unique_field,
    count(*) as n_records

from "pharmawatch_dev"."main"."stg_demo"
where primaryid is not null
group by primaryid
having count(*) > 1


