# Email templates

Every email extends `base_email.html` and fills in a few blocks.

## Minimal example

```django
{% extends "email/base_email.html" %}

{% block subject %}Your subject{% endblock %}
{% block preheader %}Preview text — shows up next to the subject in the inbox.{% endblock %}
{% block badge %}
  <span style="...pill styling...">Status</span>   {# optional #}
{% endblock %}
{% block header_title %}Your headline{% endblock %}
{% block intro %}A short paragraph under the headline.{% endblock %}

{% block content %}
  <tr><td style="padding:20px 30px;">...your content, table-based...{% endfor %}</tr>
{% endblock %}