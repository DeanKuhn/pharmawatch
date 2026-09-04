
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select case_count
from "pharmawatch_dev"."main"."mart_ror"
where case_count is null



  
  
      
    ) dbt_internal_test