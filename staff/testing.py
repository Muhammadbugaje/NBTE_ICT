"""Shared helpers for the nominal-roll release tests (staff/test_*.py).

build_sample_workbook() returns an .xlsx (bytes) with the same shapes as the
real HR file: title rows, department section headings in column A and F,
repeated header rows, an untitled older sheet, a NEW STAFF sheet without a
header row, an empty Sheet1, date typos and a duplicated FILE NO.
"""
from __future__ import annotations

import io
from datetime import datetime

from django.contrib.auth.models import Permission

HEADER = ["S/NO", "SURNAME", "OTHER NAME", "SEX", "FILE NO", "ST_ORIGIN", "LGA", "DESIGNATION",
          "CONTEDISS/CONHESS", "DEPARTMT", "D_BIRTH", "D_FAPPMT", "D_JBOARD", "D_PRAPPMT", "D_COF",
          "REMARKS", "QUALIFICATION (S)"]

JABIR_QUALS = ("FSLC 2008; SSCE 2013; Diploma in computer science 2014; "
               "BSC. (computer science) 2017; MSC (Computer Science) 2019")


def build_sample_workbook() -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()

    # Newest full roll (sheet 1 in the real file)
    ws = wb.active
    ws.title = "NOMINAL ROLL WITH QUALIFICATION"
    ws.append([None, None, "NATIONAL BOARD FOR TECHNICAL EDUCATION, KADUNA"])
    ws.append([])
    ws.append([None, None, "NOMINAL ROLL AS AT  OCTOBER, 2026"])
    ws.append(["EXECUTIVE SECRETARY OFFICE"])
    ws.append(HEADER)
    ws.append([1, "BUGAJE", "IDRIS MUHAMMAD PROF.", "M", 1270, "KATSINA", "JIBIA", "EXECUTIVE SECRETARY",
               99, "ES OFFICE", "7th Mar. 58", None, "16th Mar., 21", "16- 03-21", None, "CONFIRMED",
               "B. Eng. Chem. 80, M. Sc. Chem. 88"])
    ws.append(["I CT DEPARTMENT"])
    ws.append([2, "JABIR", "JIBRIL ABDULKADIR", "M", 1413, "KANO", "MADOBI", "SNR. PROG. OFFR ", 9, "ICT",
               datetime(1996, 6, 10), datetime(2020, 3, 17), datetime(2023, 9, 28), datetime(2025, 1, 1),
               datetime(2022, 3, 17), "CONFIRMED", JABIR_QUALS])
    ws.append([3, "AMINA", "ABDULLAHI ABUBAKAR", "F", 1487, "KADUNA", "ZARIA", "PROG. OFF. II", 7, "ICT",
               "18-03-92", "29-3-23", "29-3-23", "29-3-23", None, "PROBATION", "BSc 2015"])
    ws.append(HEADER)   # repeated header mid-sheet
    ws.append([4, "MUSA", "HUSSAINA ANKA", "F", 1487, "SOKOTO", "TAMBUWAL", "PROG. OFF. II", 7, "ICT",
               "1-AUG-99", None, None, None, None, "PROBATION", None])
    ws.append([5, "SANI", "RABI", "F", 1500, "KAN0", "DALA", "HUMAN RES. OFFR. II", 7, "HAMAN RESOURCES",
               "27-6-96", "1st - MAY- 88", "1st- JULY- 22", "2-0CT-19", "5/13/96", "COMFIRMED", None])
    ws.append([6, "SA'AD", "ABDULMAJID", "M", 1501, "AK/IBOM", "UYO", "PROG. OFF. I", 8, "ICT",
               "07-Apr.76", "30-3-234", None, None, None, "CONFIRMED", None])
    ws.append([7, "BADAMASI", "MOHAMMED", "M", 1502, "NASARAWA", "LAFIA", "DEPUTY DIRECTOR", 14, "ICT",
               datetime(2026, 10, 1), datetime(2026, 1, 2), None, None, None, "PROBATION", None])
    ws.append(["SOUTH EAST ZONAL OFFICE  AwKA ANAMBRA STATE"])
    ws.append([8, "OKEKE", "CHIOMA", "F", 1503, "ANAMBRA", "AWKA SOUTH", "PROG. OFF. II", "CONHES 06",
               "ZONAL OFFICE", "13- Aprl-2018", None, None, None, None, None, None])

    # Older, untitled sheet — a single-space name, as in the real file
    old = wb.create_sheet(" ")
    old.append([None, None, "NATIONAL BOARD FOR TECHNICAL EDUCATION, KADUNA"])
    old.append([])
    old.append([None, None, "NOMINAL ROLL AS AT 1st  SEPTEMBER, 2022"])
    old.append([None, None, None, None, None, "I CT DEPARTMENT"])
    old.append(HEADER)
    old.append([1, "JABIR", "JIBRIL ABDULKADIR", "M", 1413, "KANO", "MADOBI", "PROG. OFF. I", 8, "ICT",
                datetime(1996, 6, 10), datetime(2020, 3, 17), None, datetime(2022, 1, 1), None,
                "CONFIRMED", None])
    old.append([2, "RETIRED", "OFFICER", "M", 900, "KANO", "GWALE", "DIRECTOR", 15, "ICT",
                "05-OcT.-62", "06-Sept.-07", None, None, None, "CONFIRMED", None])

    # New-staff list without a header row
    new = wb.create_sheet("NEW STAFF 2023")
    new.append([])
    new.append([1, "MOHAMMED", "SAMBO", "M", 1600, "KADUNA", "KADUNA NORTH", "ACCOUNTANT I", 8, "FINANCE",
                datetime(1986, 12, 7), datetime(2023, 3, 30), datetime(2023, 3, 30), datetime(2023, 3, 30),
                "30-3-2025", "CONFIRMED", "ND 2007"])

    wb.create_sheet("Sheet1")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def user_with_perms(email, *codenames, department=None, superuser=False):
    """A user whose role carries the given permission codenames."""
    from accounts.models import Role, User

    if superuser:
        u = User.objects.create_superuser(email=email, password="x-pass-12345")
    else:
        role = None
        if codenames:
            role = Role.objects.create(code=email.split("@")[0][:30], name=email)
            for code in codenames:
                app, _, name = code.partition(".")
                role.permissions.add(Permission.objects.get(content_type__app_label=app, codename=name))
        u = User.objects.create_user(email, "x-pass-12345", role=role)
    if department is not None:
        u.department = department
    u.must_change_password = False
    u.save()
    return u
