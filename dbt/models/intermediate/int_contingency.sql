{{ config(materialized='table') }}

with pair_counts as (

	select
		identity_key,
		reaction_pt,
		count(distinct primaryid) as a,
    count(distinct primaryid) filter (where not is_lw) as a_nolw
	
	from {{ ref('int_drug_reaction_pairs') }}

  where identity_key is not null

	group by identity_key, reaction_pt

),

drug_counts as (

	select
		identity_key,
		count(distinct primaryid) as drug_total,
    count(distinct primaryid) filter (where not is_lw) as drug_total_nolw
	
	from {{ ref('int_drug_reaction_pairs') }}

  where identity_key is not null

	group by identity_key

),

reaction_counts as (

	select
		reaction_pt,
		count(distinct primaryid) as reaction_total,
    count(distinct primaryid) filter (where not is_lw) as reaction_total_nolw
	
	from {{ ref('int_drug_reaction_pairs') }}
	group by reaction_pt

),

total_cases as (

	select 
    count(distinct primaryid) as n,
    count(distinct primaryid) filter (where not is_lw) as n_nolw

	from {{ ref('int_drug_reaction_pairs') }}

),

contingency_table as (

	select
		p.identity_key,
		p.reaction_pt,
		p.a,
    p.a_nolw,

		-- b is cases with drug but not reaction
		d.drug_total - p.a as b,
    d.drug_total_nolw - p.a_nolw as b_nolw,
		
		-- c is cases with reaction but not drug
		r.reaction_total - p.a as c,
    r.reaction_total_nolw - p.a_nolw as c_nolw,

		-- d is cases without drug or reaction
		t.n - d.drug_total - r.reaction_total + p.a as d,
    t.n_nolw - d.drug_total_nolw - r.reaction_total_nolw + p.a_nolw as d_nolw

	from pair_counts p
	join drug_counts d on p.identity_key = d.identity_key
	join reaction_counts r on p.reaction_pt = r.reaction_pt
	cross join total_cases t

)

select * from contingency_table
