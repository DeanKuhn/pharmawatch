{% macro clean_drugname(col) %}

  {%- set quals_unwrapped -%}
    regexp_replace({{ col }},
      '\(\s*(RABBIT|HORSE|EQUINE|I[- ]?131|LU[- ]?177|TC[- ]?99M)\s*\)',
      ' \1', 'g')
  {%- endset -%}

  {%- set parens_out -%}
    regexp_replace(
      regexp_replace({{ quals_unwrapped }}, '\s*\([^)]*\)', '', 'g'),
        '\s*\([^)]*$', ''
    )
  {%- endset -%}

  {%- set punct_out -%}
    regexp_replace(trim(regexp_replace({{ parens_out }}, '[.,;:\s]+$', '')), '\s+', ' ', 'g')
  {%- endset -%}

  {%- set form_out -%}
    regexp_replace({{ punct_out }}, 
      '\s+(TABLETS?|CAPSULES?|CAPLETS?|SOLUTION FOR INJECTION|INJECTION)$', '')
  {%- endset -%}

  {%- set strength -%}
    regexp_replace({{ form_out }}, '\s+[0-9][0-9./]*\s*(MG|MCG|G|ML|%)$', '')
  {%- endset -%}

  nullif({{ strength }}, '')

{% endmacro %}
