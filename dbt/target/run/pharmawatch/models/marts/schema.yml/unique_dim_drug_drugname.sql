
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    

select
    drugname as unique_field,
    count(*) as n_records

from "pharmawatch_dev"."main"."dim_drug"
where drugname is not null
group by drugname
having count(*) > 1



  
  
      
    ) dbt_internal_test