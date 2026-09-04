
  
    
    

    create  table
      "pharmawatch_dev"."main"."fct_adverse_events__dbt_tmp"
  
    as (
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
),  __dbt__cte__int_case_demographics as (
-- from demo, normalize age to years, bucket, normalize sex too

with age_normalized as (

	select
		primaryid,
		case
			when age_cod = 'YR'  then age
			when age_cod = 'DEC' then age * 10
			when age_cod = 'MON' then age / 12
			when age_cod = 'WK'  then age / 52
			when age_cod = 'DY'  then age / 365
			when age_cod = 'HR'  then age / 8760
			else null
		end as age_years,
		sex,
		reporter_country,
		event_dt,
		occp_cod

	from "pharmawatch_dev"."main"."stg_demo"

),

case_demographics as (

	select
		primaryid,
		age_years,

		case
			when age_years < 0 or age_years > 120 then 'Unknown'
			when age_years < 18 then '0-17'
			when age_years < 45 then '18-44'
			when age_years < 65 then '45-64'
			when age_years >= 65 then '65+'
			else 'Unknown'
		end as age_group,

		case upper(sex)
			when 'M' then 'Male'
			when 'F' then 'Female'
			else 'Unknown'
		end as sex,

		reporter_country,
		event_dt,
		occp_cod

	from age_normalized

)

select * from case_demographics
), case_outcomes as (

	select
		primaryid,
		max(case when outcome_code = 'DE' then 1 else 0 end) 
			as has_death,
		max(case when outcome_code = 'HO' then 1 else 0 end) 
			as has_hospitalization,
		max(case when outcome_code = 'LT' then 1 else 0 end) 
			as has_life_threatening,
		max(case when outcome_code = 'DS' then 1 else 0 end) 
			as has_disability,
		max(case when outcome_code = 'CA' then 1 else 0 end) 
			as has_congenital_anomaly,
		max(case when outcome_code = 'RI' then 1 else 0 end) 
			as has_required_intervention,
		max(case when outcome_code not in (
				'DE', 'HO', 'LT', 'DS', 'CA', 'RI'
			) then 1 else 0 end)
			as has_other_outcome

	from "pharmawatch_dev"."main"."stg_outc"

	group by primaryid

),

fct_adverse_events as (

	select
		dr.primaryid,
		md5(cast(coalesce(cast(dr.drugname as TEXT), '_dbt_utils_surrogate_key_null_') as TEXT)) 
			as drug_key,
		md5(cast(coalesce(cast(dr.reaction_pt as TEXT), '_dbt_utils_surrogate_key_null_') as TEXT)) 
			as reaction_key,
		md5(cast(coalesce(cast(de.age_group as TEXT), '_dbt_utils_surrogate_key_null_') || '-' || coalesce(cast(de.sex as TEXT), '_dbt_utils_surrogate_key_null_') || '-' || coalesce(cast(de.reporter_country as TEXT), '_dbt_utils_surrogate_key_null_') as TEXT)) as demographics_key,
		dr.drugname,
		dr.reaction_pt,
		dr.route,
		de.event_dt,
		coalesce(oc.has_death, 0) as has_death,
		coalesce(oc.has_hospitalization, 0) as has_hospitalization,
		coalesce(oc.has_life_threatening, 0) as has_life_threatening,
		coalesce(oc.has_disability, 0) as has_disability,
		coalesce(oc.has_congenital_anomaly, 0) as has_congenital_anomaly,
		coalesce(oc.has_required_intervention, 0) as has_required_intervention,
		coalesce(oc.has_other_outcome, 0) as has_other_outcome

	from __dbt__cte__int_drug_reaction_pairs as dr

	inner join __dbt__cte__int_case_demographics as de
		on de.primaryid = dr.primaryid

	left join case_outcomes as oc
		on oc.primaryid = dr.primaryid

)

select * from fct_adverse_events
    );
  
  