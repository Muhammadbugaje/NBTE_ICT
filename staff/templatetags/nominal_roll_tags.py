"""{% load nominal_roll_tags %} — helpers for the nominal-roll release.

    {% lga_datalist %}   <datalist id="nbr-lga-options"> of the 774 LGAs
                         (staff/data/lgas.json) for the staff form's LGA box.
"""
import json
from functools import lru_cache
from pathlib import Path

from django import template
from django.utils.html import format_html, format_html_join

register = template.Library()

_LGAS = Path(__file__).resolve().parents[1] / "data" / "lgas.json"


@lru_cache(maxsize=1)
def _lga_names():
    try:
        with open(_LGAS, encoding="utf-8") as fh:
            states = json.load(fh)["states"]
    except (OSError, ValueError, KeyError):
        return ()
    return tuple(sorted({name for names in states.values() for name in names}))


@register.simple_tag
def lga_datalist(element_id="nbr-lga-options"):
    options = format_html_join("", '<option value="{}"></option>', ((n,) for n in _lga_names()))
    return format_html('<datalist id="{}">{}</datalist>', element_id, options)
