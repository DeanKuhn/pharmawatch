
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    

select
    outcome_key as unique_field,
    count(*) as n_records

from "pharmawatch_dev"."main"."dim_outcome"
where outcome_key is not null
group by outcome_key
having count(*) > 1



  
  
      
    ) dbt_internal_test