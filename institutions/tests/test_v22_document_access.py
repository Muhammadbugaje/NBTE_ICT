"""v2.2: a statutory document is seen only by its reviewing department (plus
ES, super admin, Director of ICT; the ICT officer overseeing the institution
watches read-only)."""
import pytest

pytestmark = pytest.mark.django_db


def _depts_in_checklist(inst):
    from institutions import services

    return {r["department"].code for r in services.checklist(inst) if r["department"] is not None}


def test_other_departments_get_locked_rows(proposed_inst, p2, make_user):
    from institutions import document_access, services

    rows = services.checklist(proposed_inst)
    depts = {r["department"].pk: r["department"] for r in rows if r["department"] is not None}
    assert len(depts) >= 2, "the checklist needs documents for at least two departments"
    dept = next(iter(depts.values()))
    from staff.models import Department
    import org

    director = make_user(f"v22.dir.{dept.code}@t.local", role="director", dept=dept.code)
    if org.director_of(dept) is None:
        org.assign_director(dept, director, actor=None)
    else:
        director = org.director_of(dept)
    visible, hidden = document_access.filter_rows(director, proposed_inst, rows)
    assert hidden == sum(1 for r in rows if r["department"] is None or r["department"].pk != dept.pk)
    for r in visible:
        if r.get("locked"):
            assert r["document"] is None
        else:
            assert r["department"].pk == dept.pk


def test_es_and_ict_director_see_all(proposed_inst, make_user):
    from institutions import document_access, services
    from qa_core.departments import qa_department
    import org

    rows = services.checklist(proposed_inst)
    es = make_user("v22.es.doc@t.local", role="executive-secretary")
    assert document_access.filter_rows(es, proposed_inst, rows)[1] == 0
    ict = qa_department("ict")
    dict_ = org.director_of(ict) or make_user("v22.dict@t.local", role="director", dept="ict")
    if org.director_of(ict) is None:
        org.assign_director(ict, dict_, actor=None)
    assert document_access.filter_rows(dict_, proposed_inst, rows)[1] == 0


def test_plain_officer_of_another_department_sees_nothing(proposed_inst, make_user):
    from institutions import document_access, services

    officer = make_user("v22.hr.officer@t.local", role="officer", dept="hr")
    rows, hidden = document_access.filter_rows(officer, proposed_inst, services.checklist(proposed_inst))
    assert hidden == len(rows)
