
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select drugname
from "pharmawatch_dev"."main"."mart_prr"
where drugname is null



  
  
      
    ) dbt_internal_test