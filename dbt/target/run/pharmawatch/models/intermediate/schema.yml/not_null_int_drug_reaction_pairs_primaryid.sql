
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



with __dbt__cte__int_drug_reaction_pairs as (
-- join drug on reactions on pid, primary suspect only
-- grain = one row per pdi, name, and reaction
-- IMPORTANT: separate doses per drug will be merged into one via group by

with drug_reaction_pairs as (

	select
		d.primaryid,
		d.drugname,
		max(d.route) as route,
		r.reaction_pt

	from "pharmawatch_dev"."main"."stg_drug" as d

	inner join "pharmawatch_dev"."main"."stg_reac" as r
		on d.primaryid = r.primaryid

	where d.role_cod = 'PS'

	group by d.primaryid, d.drugname, r.reaction_pt

)

select * from drug_reaction_pairs
) select primaryid
from __dbt__cte__int_drug_reaction_pairs
where primaryid is null



  
  
      
    ) dbt_internal_test