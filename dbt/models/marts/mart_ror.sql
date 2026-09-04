with pair_counts as (

	select
		drugname,
		reaction_pt,
		count(distinct primaryid) as a
	
	from {{ ref('int_drug_reaction_pairs') }}
	group by drugname, reaction_pt

),

-- count the number of pids that go with each drug
drug_counts as (

	select
		drugname,
		count(distinct primaryid) as drug_total
	
	from {{ ref('int_drug_reaction_pairs') }}
	group by drugname

),

-- count the number of pids that go with each reaction
reaction_counts as (

	select
		reaction_pt,
		count(distinct primaryid) as reaction_total
	
	from {{ ref('int_drug_reaction_pairs') }}
	group by reaction_pt

),

-- finally, select all primaryids, or total cases
total_cases as (

	select count(distinct primaryid) as n

	from {{ ref('int_drug_reaction_pairs') }}

),

contingency_table as (

	select
		p.drugname,
		p.reaction_pt,
		p.a,

		-- b is cases with drug but not reaction
		d.drug_total - p.a as b,
		
		-- c is cases with reaction but not drug
		r.reaction_total - p.a as c,

		-- d is total cases without drug or reaction individually but with pair
		t.n - d.drug_total - r.reaction_total + p.a as d

	from pair_counts p
	join drug_counts d on p.drugname = d.drugname
	join reaction_counts r on p.reaction_pt = r.reaction_pt
	cross join total_cases t

),

ror as (

	select
		drugname,
		reaction_pt,
		a as case_count,
		cast(a * d as double) / (b * c) as ror,

		exp(ln(cast(a * d as double) / (b * c)) - 1.96 * 
			sqrt(1.0/a + 1.0/b + 1.0/c + 1.0/d)) as ror_lower,

		exp(ln(cast(a * d as double) / (b * c)) + 1.96 * 
			sqrt(1.0/a + 1.0/b + 1.0/c + 1.0/d)) as ror_upper

	from contingency_table

	where a >= 3 and b > 0 and c > 0 and d > 0
		
)

select * from ror
