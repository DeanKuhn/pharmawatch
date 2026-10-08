with rxcui as (

  select
    piece,
    count(rxcui) as n

  from main.stg_rxnav_rxcui

  group by piece

  having count(rxcui) > 1

)

select n, count(*) as group_n from rxcui group by n order by n
