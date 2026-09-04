
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select primaryid
from "pharmawatch_dev"."main"."fct_adverse_events"
where primaryid is null



  
  
      
    ) dbt_internal_test