
  
    
    

    create  table
      "pharmawatch_dev"."main"."dim_reaction__dbt_tmp"
  
    as (
      -- one row per unique reaction

with  __dbt__cte__int_drug_reaction_pairs as (
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
), dim_reaction as (

	select distinct
		md5(cast(coalesce(cast(reaction_pt as TEXT), '_dbt_utils_surrogate_key_null_') as TEXT)) as reaction_key,
		reaction_pt

	from __dbt__cte__int_drug_reaction_pairs

)

select * from dim_reaction
    );
  
  