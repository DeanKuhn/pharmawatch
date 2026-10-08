with signals as (

  select
    identity_key,
    reaction_pt,

    a, b, c, d, 
    a + b + c + d as n,
    cast((a + b) * (a + c) / n as double) as expected,
    log2((a + 0.5) / (expected + 0.5)) as ic,
    ic - 3.3 * pow((a + 0.5), (-0.5)) - 2 * pow((a + 0.5), (-1.5)) as ic025,

    a_nolw, b_nolw, c_nolw, d_nolw,
    a_nolw + b_nolw + c_nolw + d_nolw as n_nolw,
    cast((a_nolw + b_nolw) * (a_nolw + c_nolw) / n_nolw as double) as expected_nolw,
    log2((a_nolw + 0.5) / (expected_nolw + 0.5)) as ic_nolw,
    ic_nolw - 3.3 * pow((a_nolw + 0.5), (-0.5)) - 2 * pow((a_nolw + 0.5), (-1.5)) 
      as ic025_nolw,

    (a - a_nolw) / a as pct_lw

  from {{ ref('int_contingency') }}

),

quarter_counts as (

  select
    identity_key,
    reaction_pt, 
    report_quarter,
    count(distinct primaryid) as q_n

  from {{ ref('int_drug_reaction_pairs') }}

  where identity_key is not null

  group by identity_key, reaction_pt, report_quarter

),

peak as (

  select
    identity_key,
    reaction_pt,
    max(q_n) as peak_quarter_n,
    arg_max(report_quarter, q_n) as peak_quarter

  from quarter_counts

  group by identity_key, reaction_pt

),

final as (

  select
    s.*,
    p.peak_quarter_n,
    p.peak_quarter,
    p.peak_quarter_n / s.a as peak_quarter_share

  from signals s
  left join peak p on s.identity_key = p.identity_key
    and s.reaction_pt = p.reaction_pt

)

select * from final
