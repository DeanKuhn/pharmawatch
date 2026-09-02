-- one row per unique age, sex, and reporter country

with dim_demographics as (

	select distinct
		{{ dbt_utils.generate_surrogate_key([
			'age_group', 'sex', 'reporter_country'
		]) }} as demographics_key,

		age_group,
		sex,
		reporter_country
		
	from {{ ref('int_case_demographics') }}

)

select * from dim_demographics
