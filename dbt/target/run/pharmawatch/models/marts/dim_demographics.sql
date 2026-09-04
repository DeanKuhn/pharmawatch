
  
    
    

    create  table
      "pharmawatch_dev"."main"."dim_demographics__dbt_tmp"
  
    as (
      -- one row per unique age, sex, and reporter country

with  __dbt__cte__int_case_demographics as (
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
), dim_demographics as (

	select distinct
		md5(cast(coalesce(cast(age_group as TEXT), '_dbt_utils_surrogate_key_null_') || '-' || coalesce(cast(sex as TEXT), '_dbt_utils_surrogate_key_null_') || '-' || coalesce(cast(reporter_country as TEXT), '_dbt_utils_surrogate_key_null_') as TEXT)) as demographics_key,

		age_group,
		sex,
		reporter_country
		
	from __dbt__cte__int_case_demographics

)

select * from dim_demographics
    );
  
  