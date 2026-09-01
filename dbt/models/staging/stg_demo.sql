with deduped as (

	select * from {{ source('faers', 'demo') }}

),

demo as (

	select
		primaryid,
		caseid,
		coalesce(try_cast(caseversion as integer), 0) as caseversion,
		i_f_code,
		{{ parse_faers_date('event_dt') }} as event_dt,
		{{ parse_faers_date('fda_dt') }} as fda_dt,
		{{ parse_faers_date('init_fda_dt') }} as init_fda_dt,
		{{ parse_faers_date('death_dt') }} as death_dt,
		try_cast(age as decimal) as age,
		age_cod,
		age_grp,
		sex,
		try_cast(wt as decimal) as wt,
		wt_cod,
		rept_cod,
		occp_cod,
		reporter_country,
		occr_country,
		mfr_sndr

	from deduped

)

select * from demo
