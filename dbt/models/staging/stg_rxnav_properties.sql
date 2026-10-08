with properties as (

  select
    response.properties.rxcui as rxcui,
    response.properties.name as rxcui_name,
    response.properties.tty as tty

  from {{ source('rxnav', 'properties') }}

)

select * from properties
