with prr as (

	select
		drugname,
		reaction_pt,
		a, b, c, d,

		(cast(a as double) / (a + b)) / (cast(c as double) / (c + d)) as prr,

		exp(ln((cast(a as double) / (a + b)) / (cast(c as double) / (c + d)))
      - 1.96 * sqrt(1.0/a - 1.0/(a+b) + 1.0/c - 1.0/(c+d))) as prr_lower,

    exp(ln((cast(a as double) / (a + b)) / (cast(c as double) / (c + d)))
      + 1.96 * sqrt(1.0/a - 1.0/(a+b) + 1.0/c - 1.0/(c+d))) as prr_upper,

    (a + b + c + d)
      * power(greatest(abs(cast(a as double) * d - cast(b as double) * c)
          - (a + b + c + d) / 2.0, 0), 2)
            / (cast(a + b as double) * (c + d) * (a + c) * (b + d)) as chi_square

  from {{ ref('int_contingency') }}

	where a >= 3 and b > 0 and c > 0 and d > 0


)

select
  *,
  prr >= 2 and chi_square >= 4 and a >= 3 as is_evans_signal
  
from prr
