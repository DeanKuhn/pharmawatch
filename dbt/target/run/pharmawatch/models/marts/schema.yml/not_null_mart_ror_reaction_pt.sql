
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select reaction_pt
from "pharmawatch_dev"."main"."mart_ror"
where reaction_pt is null



  
  
      
    ) dbt_internal_test