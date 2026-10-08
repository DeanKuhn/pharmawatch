with ror as (

	select
		identity_key,
		reaction_pt,
		a, b, c, d, 

		cast(a * d as double) / (b * c) as ror,

		exp(ln(cast(a * d as double) / (b * c)) - 1.96 * 
			sqrt(1.0/a + 1.0/b + 1.0/c + 1.0/d)) as ror_lower,

		exp(ln(cast(a * d as double) / (b * c)) + 1.96 * 
			sqrt(1.0/a + 1.0/b + 1.0/c + 1.0/d)) as ror_upper

	from {{ ref('int_contingency') }}

	where a >= 3 and b > 0 and c > 0 and d > 0
		
)

select * from ror
