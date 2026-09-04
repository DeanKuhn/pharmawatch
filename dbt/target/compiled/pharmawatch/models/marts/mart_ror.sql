-- count the number of pids per drug + reaction pair
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
), pair_counts as (

	select
		drugname,
		reaction_pt,
		count(distinct primaryid) as a
	
	from __dbt__cte__int_drug_reaction_pairs
	group by drugname, reaction_pt

),

-- count the number of pids that go with each drug
drug_counts as (

	select
		drugname,
		count(distinct primaryid) as drug_total
	
	from __dbt__cte__int_drug_reaction_pairs
	group by drugname

),

-- count the number of pids that go with each reaction
reaction_counts as (

	select
		reaction_pt,
		count(distinct primaryid) as reaction_total
	
	from __dbt__cte__int_drug_reaction_pairs
	group by reaction_pt

),

-- finally, select all primaryids, or total cases
total_cases as (

	select count(distinct primaryid) as n

	from __dbt__cte__int_drug_reaction_pairs

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