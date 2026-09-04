
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select reaction_key
from "pharmawatch_dev"."main"."dim_reaction"
where reaction_key is null



  
  
      
    ) dbt_internal_test