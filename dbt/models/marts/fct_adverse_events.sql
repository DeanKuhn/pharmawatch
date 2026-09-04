with case_outcomes as (

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

	from {{ ref('stg_outc') }}

	group by primaryid

),

fct_adverse_events as (

	select
		dr.primaryid,
		{{ dbt_utils.generate_surrogate_key(['dr.drugname']) }} 
			as drug_key,
		{{ dbt_utils.generate_surrogate_key(['dr.reaction_pt']) }} 
			as reaction_key,
		{{ dbt_utils.generate_surrogate_key([
			'de.age_group', 'de.sex', 'de.reporter_country'
		]) }} as demographics_key,
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

	from {{ ref('int_drug_reaction_pairs') }} as dr

	inner join {{ ref('int_case_demographics') }} as de
		on de.primaryid = dr.primaryid

	left join case_outcomes as oc
		on oc.primaryid = dr.primaryid

)

select * from fct_adverse_events
