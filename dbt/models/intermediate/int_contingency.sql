{{ config(materialized='table') }}

with pair_counts as (

	select
		identity_key,
		reaction_pt,
		count(distinct primaryid) as a
	
	from {{ ref('int_drug_reaction_pairs') }}

  where identity_key is not null

	group by identity_key, reaction_pt

),

drug_counts as (

	select
		identity_key,
		count(distinct primaryid) as drug_total
	
	from {{ ref('int_drug_reaction_pairs') }}

  where identity_key is not null

	group by identity_key

),

reaction_counts as (

	select
		reaction_pt,
		count(distinct primaryid) as reaction_total
	
	from {{ ref('int_drug_reaction_pairs') }}
	group by reaction_pt

),

total_cases as (

	select count(distinct primaryid) as n

	from {{ ref('int_drug_reaction_pairs') }}

),

contingency_table as (

	select
		p.identity_key,
		p.reaction_pt,
		p.a,

		-- b is cases with drug but not reaction
		d.drug_total - p.a as b,
		
		-- c is cases with reaction but not drug
		r.reaction_total - p.a as c,

		-- d is cases without drug or reaction
		t.n - d.drug_total - r.reaction_total + p.a as d

	from pair_counts p
	join drug_counts d on p.identity_key = d.identity_key
	join reaction_counts r on p.reaction_pt = r.reaction_pt
	cross join total_cases t

)

select * from contingency_table
