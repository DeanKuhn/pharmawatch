
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select outcome_key
from "pharmawatch_dev"."main"."dim_outcome"
where outcome_key is null



  
  
      
    ) dbt_internal_test