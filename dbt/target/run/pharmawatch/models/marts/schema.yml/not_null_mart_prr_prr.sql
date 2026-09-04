
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select prr
from "pharmawatch_dev"."main"."mart_prr"
where prr is null



  
  
      
    ) dbt_internal_test