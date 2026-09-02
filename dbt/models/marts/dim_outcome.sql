-- one row per unique outcome_code (ft. decoded label)

with dim_outcome as (

	select distinct
		{{ dbt_utils.generate_surrogate_key(['outcome_code']) }} as outcome_key,
		outcome_code,
		
		case
			when outcome_code = 'DE' then 'Death'
			when outcome_code = 'HO' then 'Hospitalization'
			when outcome_code = 'LT' then 'Life-Threatening'
			when outcome_code = 'DS' then 'Disability'
			when outcome_code = 'CA' then 'Congenital Anomaly'
			when outcome_code = 'RI' then 'Required Intervention'
			else 'Other'
		end as outcome_desc 

	from {{ ref('stg_outc') }}

)

select * from dim_outcome

