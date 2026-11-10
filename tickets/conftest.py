"""Phase C ticket fixtures: reuse the ICT org of tasks/conftest.py (director
seat, deputy, division head, officers, ES) and add Finance + a printer."""
import pytest

from tasks.conftest import depts, fresh, mk, people, roles  # noqa: F401


@pytest.fixture
def org_c(people, depts):
    import org
    from assets import services as asvc
    from assets.models import AssetCategory
    from staff.models import Department
    from tickets.models import TicketCategory

    fin = Department.objects.create(name="Finance (t)", code="finance")
    fin_dir = mk("findir@t.local", "director", fin)
    fin_officer = mk("finofficer@t.local", "officer", fin)
    Department.objects.filter(pk=fin.pk).update(director=fin_dir)
    cat = TicketCategory.objects.create(code="hw-c", name="Hardware", minimum_priority="normal",
                                        handling_department=depts["ict"])
    acat = AssetCategory.objects.create(name="Printer (t)", code="printer-t")
    printer = asvc.create_asset(category=acat, owner_department=fin, asset_tag="T-PRN-1", brand="HP",
                                model="Test", state="in_service")
    asvc.assign_asset(asset=printer, to_user=fin_officer, by_user=fin_dir)
    return {**people, "fin": fin, "fin_dir": fresh(fin_dir), "fin_officer": fresh(fin_officer), "cat": cat,
            "printer": printer, "ict": depts["ict"], "org": org}


@pytest.fixture
def ticket(org_c):
    from tickets import services
    return services.create_ticket(raised_by=org_c["fin_officer"], category=org_c["cat"], subject="Printer blank",
                                  body="Prints blank pages since Monday.", asset=org_c["printer"])
