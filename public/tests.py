from datetime import date
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.template.loader import render_to_string

from public.services import leadership_page_context
from staff.models import Department, ExecutivePosition, ExecutiveTenure, Staff


pytestmark = pytest.mark.django_db


def test_leadership_page_context_matches_public_template():
    cache.clear()
    department = Department.objects.create(name="Information Technology")
    staff = Staff.objects.create(
        employee_no="LEADER-001",
        first_name="Ada",
        last_name="Lovelace",
        department=department,
    )
    position = ExecutivePosition.objects.create(
        name="Director of Information Technology",
        department=department,
        order=1,
    )
    ExecutiveTenure.objects.create(
        position=position,
        staff=staff,
        started_on=date(2024, 1, 1),
        is_public=True,
    )

    context = leadership_page_context()

    assert context["leadership"] == list(context["departments"][0]["tenures"])
    assert context["leadership"][0].staff.full_name == "Ada Lovelace"
    assert context["departments_with_leadership"] == [
        {"department": department, "count": 1},
    ]
    assert context["total"] == 1

    html = render_to_string(
        "public/leadership.html",
        {
            **context,
            "site_info": SimpleNamespace(
                organisation_name="NBTE",
                department_name="ICT",
                footer_note="",
            ),
        },
    )
    assert "Ada Lovelace" in html
    # Review L9: the filter uses the department's generated code.
    assert f'data-gov-filter="{department.code}"' in html
