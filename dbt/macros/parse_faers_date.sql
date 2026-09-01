{% macro parse_faers_date(column_name) %}
case
    when length({{ column_name }}) = 8 then try_cast({{ column_name }} as date)
    when length({{ column_name }}) = 6 then try_cast({{ column_name }} || '01' as date)
    when length({{ column_name }}) = 4 then try_cast({{ column_name }} || '0101' as date)
    else null
end
{% endmacro %}
