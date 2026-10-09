import pytest

from core.importexport import Column, ExportSpec, ImportSpec, REGISTRY, register
from staff.models import Department

pytestmark = pytest.mark.django_db


@register
class DeptImport(ImportSpec):
    name = "test-dept"
    key_fields = ("name",)
    columns = [
        Column("Name", "name", required=True),
        Column("Sort order", "sort_order", parse=int),
    ]

    def validate_row(self, cleaned):
        from django.core.exceptions import ValidationError
        if cleaned["name"].lower() == "forbidden":
            raise ValidationError("Name is not allowed")

    def save_row(self, cleaned):
        _, created = Department.objects.update_or_create(
            name=cleaned["name"], defaults={"sort_order": cleaned["sort_order"] or 0})
        return "created" if created else "updated"


ROWS = [
    {"Name": "ICT", "Sort order": "1"},
    {"Name": "", "Sort order": "2"},                 # missing required
    {"Name": "Legal", "Sort order": "abc"},          # bad int
    {"Name": "Forbidden", "Sort order": "3"},        # cross-field rule
    {"Name": "Admin", "Sort order": "4", "Bogus": "x"},  # unknown column
    {"Name": "ICT", "Sort order": "9"},              # update of row 1
]


def test_good_rows_saved_bad_rows_rejected_with_reasons():
    res = DeptImport().run(ROWS)
    assert (res.created, res.updated, len(res.rejected)) == (1, 1, 4)
    assert Department.objects.get(name="ICT").sort_order == 9
    by_row = {r.row_number: r.errors for r in res.rejected}
    assert by_row[2] == ["Name is required"]
    assert "Sort order" in by_row[3][0]
    assert by_row[4] == ["Name is not allowed"]
    assert "Unknown column" in by_row[5][0]
    assert not res.ok


def test_dry_run_changes_nothing():
    res = DeptImport().run(ROWS[:1], dry_run=True)
    assert res.created == 1 and res.dry_run
    assert not Department.objects.exists()


def test_rejected_table_shape_for_the_report_writer():
    spec = DeptImport()
    res = spec.run(ROWS)
    header, rows = res.rejected_table(spec.columns)
    assert header == ["Row", "Name", "Sort order", "Errors"]
    assert rows[0][0] == 2 and rows[0][-1] == "Name is required"


def test_db_error_in_one_row_does_not_poison_the_batch():
    class Boom(DeptImport):
        name = "boom"

        def save_row(self, cleaned):
            if cleaned["name"] == "X":
                raise RuntimeError("db exploded")
            return super().save_row(cleaned)

    res = Boom().run([{"Name": "X"}, {"Name": "Y"}])
    assert res.created == 1 and res.rejected[0].errors == ["Could not save: db exploded"]
    assert Department.objects.filter(name="Y").exists()


def test_export_spec_and_registry():
    Department.objects.create(name="B", sort_order=2)
    Department.objects.create(name="A", sort_order=1)

    @register
    class DeptExport(ExportSpec):
        name = "test-dept-export"
        columns = [Column("Name", "name"), Column("Sort order", "sort_order")]

        def get_queryset(self):
            return Department.objects.order_by("name")

    spec = REGISTRY["export"]["test-dept-export"]()
    assert spec.header() == ["Name", "Sort order"]
    assert list(spec.rows()) == [["A", 1], ["B", 2]]
    assert "test-dept" in REGISTRY["import"]
