
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    

with all_values as (

    select
        outcome_code as value_field,
        count(*) as n_records

    from "pharmawatch_dev"."main"."dim_outcome"
    group by outcome_code

)

select *
from all_values
where value_field not in (
    'DE','HO','LT','DS','CA','RI','OT'
)



  
  
      
    ) dbt_internal_test