"""Phase 8: safe orphan scanner, per-institution media folders and the
organize_institution_media command. Every test runs on temporary media roots
(the root conftest points MEDIA_ROOT / PROTECTED_MEDIA_ROOT at tmp_path)."""
import io
import os
import time
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models
from django.utils import timezone

from core import media_scanner as scanner
from qa_core.files import institution_folder, institution_upload_to, safe_folder_name
from qa_core.tests.phase2_fixtures import *  # noqa: F401,F403

OLD = time.time() - 400 * 86400


def _write(root, rel, data=b"%PDF-1.4 test", age=OLD):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (age, age))
    return path


def _age(path, when=OLD):
    os.utime(path, (when, when))


def _orphans(result):
    return {(o["kind"], o["rel"]): o for o in result.orphans}


@pytest.fixture
def slip(bill):
    from payments import services

    current = services.current_slip(bill) or services.generate_slip(bill)
    return current


# ---------------------------------------------------------------------------
# Folder names and upload_to
# ---------------------------------------------------------------------------
def test_safe_folder_name_keeps_spaces_and_strips_odd_characters():
    assert safe_folder_name("Kaduna Demo / Polytechnic") == "Kaduna Demo Polytechnic"
    assert safe_folder_name("..\\evil<>:|?*name") == "evilname"
    assert safe_folder_name("A" * 300) == "A" * 80
    assert safe_folder_name("   ") == "unnamed"


def test_institution_folder_is_code_dash_name(make_institution, qa_seed):
    inst = make_institution("KDP", name="Kaduna Demo Polytechnic")
    assert institution_folder(inst) == "KDP - Kaduna Demo Polytechnic"


def test_new_slip_lands_in_the_institution_payments_folder(slip, settings):
    inst = slip.obligation.institution
    assert slip.file.name.startswith(f"institutions/{institution_folder(inst)}/payments/slips/")
    assert (Path(settings.PROTECTED_MEDIA_ROOT) / slip.file.name).is_file()
    assert not (Path(settings.MEDIA_ROOT) / slip.file.name).exists()


def test_upload_to_falls_back_without_an_institution():
    from payments.models import PaymentSlip

    name = institution_upload_to(PaymentSlip(), "Some Slip.PDF")
    assert name.startswith("qa/slips/") and name.endswith(".pdf")


def test_resource_person_files_go_under_their_name():
    from inspection.models import ResourcePerson, ResourcePersonFile

    person = ResourcePerson(pk=5, full_name="Dr. Ada / Obi")
    f = ResourcePersonFile(resource_person=person)
    f.resource_person_id = 5
    assert institution_upload_to(f, "cv.pdf").startswith("resource-persons/Dr. Ada Obi/")


# ---------------------------------------------------------------------------
# Orphan scanner
# ---------------------------------------------------------------------------
def test_files_in_use_are_never_orphans_but_a_stray_old_file_is(slip, settings):
    from staff.models import Staff, StaffAttachment
    from django.core.files.uploadedfile import SimpleUploadedFile

    protected = Path(settings.PROTECTED_MEDIA_ROOT)
    public = Path(settings.MEDIA_ROOT)

    # 1. a slip referenced by a bill (scoped model, default manager refuses unscoped reads)
    _age(protected / slip.file.name)
    # 2. a generated guide PDF in a protected folder (no DB row at all)
    _write(public, "qa/guides/nbte-qa-starter-guide.docx")
    # 3. a file referenced only from a JSON field
    _write(protected, "legacy/scans/7f1e2d3c4b5a69788796a5b4c3d2e1f0.pdf")
    models.QuerySet(model=type(slip)).filter(pk=slip.pk).update(
        bank_snapshot={"bank_name": "X", "scan": "legacy/scans/7f1e2d3c4b5a69788796a5b4c3d2e1f0.pdf"})
    # 4. a soft-deleted row's file
    staff = Staff.objects.create(employee_no="A1", first_name="A", last_name="B")
    att = StaffAttachment.objects.create(staff=staff, description="old",
                                         file=SimpleUploadedFile("c.pdf", b"%PDF-x"))
    att.delete()
    assert StaffAttachment.all_objects.get(pk=att.pk).is_deleted
    _age(protected / att.file.name)
    # 5. a truly unreferenced old file, and 6. an unreferenced brand-new one
    _write(protected, "qa/slips/0123456789abcdef0123456789abcdef.pdf")
    _write(protected, "qa/slips/fedcba9876543210fedcba9876543210.pdf", age=time.time())

    result = scanner.scan(min_age_days=30)
    orphans = _orphans(result)

    assert ("protected", slip.file.name) not in orphans
    assert ("public", "qa/guides/nbte-qa-starter-guide.docx") not in orphans
    assert ("protected", "legacy/scans/7f1e2d3c4b5a69788796a5b4c3d2e1f0.pdf") not in orphans
    assert ("protected", att.file.name) not in orphans
    assert ("protected", "qa/slips/fedcba9876543210fedcba9876543210.pdf") not in orphans
    stray = orphans[("protected", "qa/slips/0123456789abcdef0123456789abcdef.pdf")]
    assert "No database record" in stray["reason"]
    assert len(result.orphans) == 1
    assert len(result.recent_unreferenced) == 1

    index = scanner.build_reference_map()
    assert "PaymentSlip" in index.reference_for("protected", slip.file.name)
    assert "protected folder" in index.reference_for("public", "qa/guides/nbte-qa-starter-guide.docx")
    assert "bank_snapshot" in index.reference_for("protected", "legacy/scans/7f1e2d3c4b5a69788796a5b4c3d2e1f0.pdf")


def test_document_and_image_filefield_references_are_not_orphans(db, settings):
    from django.contrib.auth import get_user_model
    from django.core.files.uploadedfile import SimpleUploadedFile
    from documents.models import StaffSignature
    from portal.models import Document, DocumentCategory, DocumentVersion
    from public.models import HistoryMilestone
    from showcase.models import ShowcaseImage

    owner = get_user_model().objects.create_user("media-scan-documents@t.local", password="unused")
    category = DocumentCategory.objects.create(name="Scanner regression")
    document = Document.objects.create(title="Scanner regression document", category=category, owner=owner)
    version = DocumentVersion.objects.create(
        document=document,
        version_number=1,
        file=SimpleUploadedFile("scanner-regression.pdf", b"%PDF-1.4 document"),
    )
    image = ShowcaseImage.objects.create(
        image=SimpleUploadedFile("scanner-regression.png", b"stored image bytes"),
        alt_text="Scanner regression image",
        placements="hero",
    )
    # A second FileField model ensures references are discovered across models,
    # not just the models used by the scanner's historic cleanup registry.
    signature = StaffSignature.objects.create(
        user=owner,
        image=SimpleUploadedFile("scanner-signature.png", b"stored signature bytes"),
    )
    milestone = HistoryMilestone.objects.create(
        year="2026",
        title="Scanner regression milestone",
        image=SimpleUploadedFile("scanner-history.png", b"stored public image bytes"),
    )

    referenced = [
        ("protected", version.file.name),
        ("protected", image.image.name),
        ("protected", signature.image.name),
        ("public", milestone.image.name),
    ]
    for kind, rel in referenced:
        root = settings.PROTECTED_MEDIA_ROOT if kind == "protected" else settings.MEDIA_ROOT
        _age(Path(root) / rel)

    index = scanner.build_reference_map()

    for kind, rel in referenced:
        root = settings.PROTECTED_MEDIA_ROOT if kind == "protected" else settings.MEDIA_ROOT
        entry = scanner.FileEntry(kind=kind, rel=rel, abs=Path(root) / rel,
                                  size=(Path(root) / rel).stat().st_size, mtime=OLD)
        is_orphan, reason = scanner.classify(entry, index, min_age_days=0)
        assert not is_orphan, reason
    assert "DocumentVersion" in index.reference_for(*referenced[0])
    assert "ShowcaseImage" in index.reference_for(*referenced[1])
    assert "StaffSignature" in index.reference_for(*referenced[2])
    assert "HistoryMilestone" in index.reference_for(*referenced[3])


def test_guide_and_configured_static_asset_references_are_kept(db, settings, tmp_path):
    static_root = tmp_path / "configured-static"
    static_root.mkdir()
    settings.STATICFILES_DIRS = [static_root]

    guide_image = "guide-assets/scanner-regression-guide-image.png"
    _write(settings.MEDIA_ROOT, guide_image)
    (static_root / "guide-regression.html").write_text(
        f'<img src="/media/{guide_image}">', encoding="utf-8"
    )
    guide_document = "qa/guides/scanner-regression-guide.pdf"
    _write(settings.MEDIA_ROOT, guide_document)
    excluded_paths = [
        "static/scanner-regression.css",
        "fixtures/scanner-regression.json",
        "system/scanner-regression.dat",
    ]
    for rel in excluded_paths:
        _write(settings.MEDIA_ROOT, rel)

    index = scanner.build_reference_map()
    stored_paths = {(entry.kind, entry.rel) for entry in scanner.walk_storage()}

    assert "static/guide asset" in index.reference_for("public", guide_image)
    assert "protected folder" in index.reference_for("public", guide_document)
    for rel in (guide_image, guide_document):
        entry = scanner.FileEntry(kind="public", rel=rel, abs=Path(settings.MEDIA_ROOT) / rel,
                                  size=(Path(settings.MEDIA_ROOT) / rel).stat().st_size, mtime=OLD)
        is_orphan, reason = scanner.classify(entry, index, min_age_days=0)
        assert not is_orphan, reason
    assert not any(("public", rel) in stored_paths for rel in excluded_paths)


def test_old_unreferenced_file_is_classified_as_an_orphan(db, settings, monkeypatch):
    orphan = "uploads/scanner-regression-unreferenced.pdf"
    path = _write(settings.PROTECTED_MEDIA_ROOT, orphan)
    monkeypatch.setattr(scanner, "build_reference_map", lambda: scanner.ReferenceIndex())
    result = scanner.scan(min_age_days=30)

    assert ("protected", orphan) in _orphans(result)
    assert path.is_file()


def test_incomplete_reference_index_fails_closed_without_moving_files(db, settings, monkeypatch):
    orphan = "uploads/scanner-regression-index-failure.pdf"
    path = _write(settings.PROTECTED_MEDIA_ROOT, orphan)
    incomplete = scanner.ReferenceIndex(complete=False, errors=["simulated field query failure"])
    monkeypatch.setattr(scanner, "build_reference_map", lambda: incomplete)

    result = scanner.scan(min_age_days=0)
    quarantine = scanner.quarantine_paths(
        [{"kind": "protected", "rel": orphan}],
        min_age_days=0,
        index=incomplete,
    )

    assert result.reference_index_complete is False
    assert result.reference_errors == incomplete.errors
    assert not result.orphans
    assert quarantine.skipped_unverified == 1
    assert path.is_file()
    assert not list(Path(settings.PROTECTED_MEDIA_ROOT).glob(".quarantine/**"))


def test_extra_protected_folders_from_config(db, settings):
    from config.models import ConfigEntry

    _write(settings.MEDIA_ROOT, "brand/logo.png")
    assert scanner.scan(min_age_days=0).orphans
    ConfigEntry.objects.update_or_create(key="files.orphan_protected_folders",
                                         defaults={"value": "public:brand", "value_type": "str"})
    from django.core.cache import cache
    cache.clear()
    assert not scanner.scan(min_age_days=0).orphans


def test_quarantine_moves_restores_and_purges(slip, settings):
    protected = Path(settings.PROTECTED_MEDIA_ROOT)
    stray = "qa/slips/0123456789abcdef0123456789abcdef.pdf"
    _write(protected, stray)
    _age(protected / slip.file.name)

    res = scanner.quarantine_paths([{"kind": "protected", "rel": stray},
                                    {"kind": "protected", "rel": slip.file.name}], min_age_days=30)
    assert res.deleted == 1 and res.skipped_in_use == 1
    day = timezone.localdate().isoformat()
    assert not (protected / stray).exists()
    assert (protected / ".quarantine" / day / stray).is_file()
    assert (protected / ".quarantine" / day / "manifest.csv").is_file()
    assert (protected / slip.file.name).is_file()       # in use: untouched
    # the quarantine is invisible to the next scan
    assert not any(o["rel"].startswith(".quarantine") for o in scanner.scan(min_age_days=0).orphans)

    restored, _ = scanner.restore_quarantine(day)
    assert restored == 1 and (protected / stray).is_file()

    scanner.quarantine_paths([{"kind": "protected", "rel": stray}], min_age_days=30)
    assert not scanner.purge_quarantine(older_than_days=30).days_removed  # too recent
    assert scanner.purge_quarantine(older_than_days=0).days_removed == [f"protected/{day}"]
    assert not (protected / ".quarantine" / day).exists()


def test_young_files_are_never_quarantined(db, settings):
    _write(settings.PROTECTED_MEDIA_ROOT, "qa/x/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf", age=time.time())
    res = scanner.quarantine_paths([{"kind": "protected", "rel": "qa/x/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf"}],
                                   min_age_days=30)
    assert res.deleted == 0 and res.skipped_young == 1


def test_scan_media_command_is_a_dry_run_with_reasons(db, settings, tmp_path):
    _write(settings.PROTECTED_MEDIA_ROOT, "qa/x/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.pdf")
    report = tmp_path / "orphans.csv"
    out = io.StringIO()
    call_command("scan_media", "--report", str(report), stdout=out)
    assert "why: No database record" in out.getvalue()
    assert "Dry run" in out.getvalue()
    assert (Path(settings.PROTECTED_MEDIA_ROOT) / "qa/x/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.pdf").is_file()
    assert "bbbbbbbb" in report.read_text()
    call_command("scan_media", "--quarantine", stdout=io.StringIO())
    assert not (Path(settings.PROTECTED_MEDIA_ROOT) / "qa/x/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.pdf").exists()


# ---------------------------------------------------------------------------
# organize_institution_media
# ---------------------------------------------------------------------------
def _to_legacy(slip, settings):
    """Put the slip back where Phase 7 stored it (qa/slips/<uuid>.pdf)."""
    root = Path(settings.PROTECTED_MEDIA_ROOT)
    legacy = f"qa/slips/{Path(slip.file.name).name}"
    (root / legacy).parent.mkdir(parents=True, exist_ok=True)
    os.replace(root / slip.file.name, root / legacy)
    models.QuerySet(model=type(slip)).filter(pk=slip.pk).update(file=legacy)
    return legacy


def test_organize_dry_run_then_apply_then_rerun(slip, settings, tmp_path):
    from payments.models import PaymentSlip

    root = Path(settings.PROTECTED_MEDIA_ROOT)
    legacy = _to_legacy(slip, settings)
    target = f"institutions/{institution_folder(slip.obligation.institution)}/payments/slips/{Path(legacy).name}"

    report = tmp_path / "dry.csv"
    out = io.StringIO()
    call_command("organize_institution_media", "--report", str(report), stdout=out)
    assert "would move 1" in out.getvalue() or "would_move" in report.read_text()
    assert (root / legacy).is_file()
    assert models.QuerySet(model=PaymentSlip).get(pk=slip.pk).file.name == legacy

    with pytest.raises(CommandError):
        call_command("organize_institution_media", "--apply", stdout=io.StringIO(), stderr=io.StringIO())

    report = tmp_path / "apply.csv"
    call_command("organize_institution_media", "--apply", "--i-have-a-backup", "--report", str(report),
                 stdout=io.StringIO())
    assert not (root / legacy).exists()
    assert (root / target).is_file()
    assert models.QuerySet(model=PaymentSlip).get(pk=slip.pk).file.name == target
    assert "moved" in report.read_text()

    report = tmp_path / "again.csv"
    call_command("organize_institution_media", "--apply", "--i-have-a-backup", "--report", str(report),
                 stdout=io.StringIO())
    assert "in_place" in report.read_text() and ",moved," not in report.read_text()
    assert models.QuerySet(model=PaymentSlip).get(pk=slip.pk).file.name == target


def test_organize_never_overwrites(slip, settings):
    from payments.models import PaymentSlip
    from qa_core.media_organize import organize

    root = Path(settings.PROTECTED_MEDIA_ROOT)
    legacy = _to_legacy(slip, settings)
    folder = f"institutions/{institution_folder(slip.obligation.institution)}/payments/slips"
    squatter = root / folder / Path(legacy).name
    squatter.parent.mkdir(parents=True, exist_ok=True)
    squatter.write_bytes(b"someone else")

    result = organize(apply=True)
    row = next(r for r in result.rows if r["model"] == "payments.PaymentSlip" and r["pk"] == slip.pk)
    assert row["action"] == "moved" and row["new_path"].endswith("-1.pdf")
    assert squatter.read_bytes() == b"someone else"
    assert models.QuerySet(model=PaymentSlip).get(pk=slip.pk).file.name == row["new_path"]
    assert (root / row["new_path"]).is_file()


def test_organize_reports_missing_files_and_leaves_the_row(slip, settings):
    from payments.models import PaymentSlip
    from qa_core.media_organize import organize

    models.QuerySet(model=PaymentSlip).filter(pk=slip.pk).update(file="qa/slips/gone.pdf")
    result = organize(apply=True)
    row = next(r for r in result.rows if r["pk"] == slip.pk and r["model"] == "payments.PaymentSlip")
    assert row["action"] == "missing"
    assert models.QuerySet(model=PaymentSlip).get(pk=slip.pk).file.name == "qa/slips/gone.pdf"


def test_apply_accepts_a_fresh_sqlite_backup(db, settings, tmp_path):
    from qa_core.management.commands.organize_institution_media import find_fresh_backup

    db_file = tmp_path / "db.sqlite3"
    db_file.write_bytes(b"x")
    conf = {"ENGINE": "django.db.backends.sqlite3", "NAME": str(db_file)}
    settings.DB_BACKUP_DIR = None
    assert find_fresh_backup(db=conf) is None
    (tmp_path / "db.sqlite3.bak").write_bytes(b"backup")
    assert find_fresh_backup(db=conf) == tmp_path / "db.sqlite3.bak"
    old = time.time() - 3 * 86400
    os.utime(tmp_path / "db.sqlite3.bak", (old, old))
    assert find_fresh_backup(db=conf) is None
