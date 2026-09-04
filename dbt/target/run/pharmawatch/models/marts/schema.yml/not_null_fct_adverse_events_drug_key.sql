
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select drug_key
from "pharmawatch_dev"."main"."fct_adverse_events"
where drug_key is null



  
  
      
    ) dbt_internal_test