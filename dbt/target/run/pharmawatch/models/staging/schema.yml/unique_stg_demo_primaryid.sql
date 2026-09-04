
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    

select
    primaryid as unique_field,
    count(*) as n_records

from "pharmawatch_dev"."main"."stg_demo"
where primaryid is not null
group by primaryid
having count(*) > 1



  
  
      
    ) dbt_internal_test