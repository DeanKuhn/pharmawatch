{% macro parse_faers_date(column_name) %}
case
    when length({{ column_name }}) = 8 then try_strptime({{ column_name }}, '%Y%m%d')::date
    when length({{ column_name }}) = 6 then try_strptime({{ column_name }} || '01', '%Y%m%d')::date
    when length({{ column_name }}) = 4 then try_strptime({{ column_name }} || '0101', '%Y%m%d')::date
    else null
end
{% endmacro %}
