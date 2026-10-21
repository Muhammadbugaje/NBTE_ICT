# SETUP — bringing a fresh database to a working state

Every command, in order. Run them from the project folder (the one with `manage.py`).

```bash
# 1. Environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # development extras: pip install -r requirements-dev.txt

# 2. Environment variables
#    Development needs none: settings/dev.py uses SQLite (db.sqlite3) and console email.
#    A .env file next to manage.py is loaded automatically if present.
#    Production (settings.prod) requires: SECRET_KEY, ALLOWED_HOSTS, DATABASE_URL, SITE_URL,
#    DEFAULT_FROM_EMAIL, REDIS_URL, CELERY_BROKER_URL.

# 3. Database
python manage.py migrate

# 4. Seed system data — run in this order
python manage.py seed_roles --sync            # roles + their ranks (--sync re-applies rank to existing roles)
python manage.py seed_config
python manage.py seed_departments             # ICT + Human Resources (stable codes used by workflows)
python manage.py seed_nbte                    # NBTE departments, designations + promotion ladder, 36 states + FCT, Confirmed/Probation
python manage.py seed_default_groups          # Stage 3 permission groups
python manage.py seed_memo_demo --routes-only  # memo routes, rules + priorities, memo permissions (see MEMO_ROUTING.md)
python manage.py showcase_seed --count 5      # Stage 2 — optional placeholder images

# 5. Admin user
python manage.py createsuperuser

# 6. Static files (production)
python manage.py collectstatic --noinput

# 7. Run
python manage.py runserver
```

### Then load the staff
(Administration → **Staff import & export** in the app. Tick “Create a sign-in account…” on the
preview to give everyone a login; include an EMAIL column so each person signs in with their email
and gets a welcome email.)
```bash
python manage.py import_nominal_roll "NOMINAL ROLL AS AT OCTOBER, 2026.xlsx" --dry-run   # check, saves nothing
python manage.py import_nominal_roll "NOMINAL ROLL AS AT OCTOBER, 2026.xlsx"             # import
python manage.py audit_designations                                                       # review ranks
```
Or, in the app: **People → Nominal roll → Import** (see `IMPORT_PLAYBOOK.md`).

Optional: `python manage.py seed_nbte --with-lgas` also checks the bundled list of 774 LGAs
(`staff/data/lgas.json`) and reports staff LGA spellings that are not on it. LGA stays a free-text
field; the list powers the autocomplete on the staff form.

All seed commands are idempotent — running them again changes nothing. `seed_nbte` only fills
blanks on existing records (a rank of 0, an empty CONTEDISS range, no next designation); it never
changes a value someone has already set in the admin.

### Upgrading an existing installation (your current `db.sqlite3`)
```bash
python manage.py migrate          # applies staff 0007–0011 (designation becomes a lookup; transfers, accounts)
python manage.py seed_nbte
```
Your existing staff keep their designation: each distinct text value becomes a Designation record
(staff 0008), and `seed_nbte` then matches the NBTE ones by spelling and ranks them.

---

## Email — check it really sends

Email goes out through Brevo (SMTP relay first, then the Brevo API), with Gmail as the fallback.
The credentials live only in `.env` (this zip contains none):

```bash
# .env
DEFAULT_FROM_EMAIL="NBTE ICT <no-reply@nbte.gov.ng>"   # must be a sender verified in Brevo
BREVO_SMTP_LOGIN=...        # Brevo → SMTP & API → SMTP login
BREVO_SMTP_KEY=...          # the SMTP key (not your password)
BREVO_API_KEY=...           # optional second route
GMAIL_USER=... GMAIL_APP_PASSWORD=...   # optional fallback (Google app password)
SITE_URL=https://ict.nbte.gov.ng        # used in links inside emails
```

Then:
```bash
python manage.py check_email                                  # checks everything, sends nothing
python manage.py check_email --send --to you@nbte.gov.ng      # sends a real test message
```
`check_email` reports the sender, and for each provider whether its host resolves, the port is
reachable, and STARTTLS + login succeed — then, with `--send`, which provider accepted the test
message. Every message is also listed in the email log (Admin → Notifications → Email logs).
In development Celery runs inline, so mail is sent straight away; in production keep
`celery -A nbte_ict worker` running. The Settings page also has a **Send test email** button.

---

## If something goes wrong

**Reverse the designation data migration.** Each step reverses on its own; the text designation
is restored exactly as it was:
```bash
python manage.py migrate staff 0009   # puts back the temporary designation_legacy column
python manage.py migrate staff 0008   # Designation FK back beside the text column
python manage.py migrate staff 0007   # text column restored from designation_legacy
python manage.py migrate staff 0006   # removes every nominal-roll table and field
```
Note: going below 0007 deletes the new data (promotions, import history, saved queries, the
nominal-roll fields). Back up `db.sqlite3` first.

**Start again with a clean database (development):**
```bash
# stop the server, then
mv db.sqlite3 db.sqlite3.bak      # or delete it
python manage.py migrate
# …and repeat step 4 onwards
```

**Where import reports land.** Every confirmed import writes a text report to the protected
media folder: `protected_media/staff/imports/reports/<random>.txt` (or under
`PROTECTED_MEDIA_ROOT` if you set it). Download them from **People → Nominal roll → History** —
never from a raw URL. The command line also writes `<file>.xlsx.report.txt` next to the workbook.
Uploaded workbooks waiting for confirmation sit in `protected_media/staff/imports/uploads/` and are
deleted when the import is confirmed or discarded.

**The import stopped.** Nothing was saved (the whole import is one transaction). The message and the
report say which sheet, row and value caused it. Fix the cell and upload again.

**A designation has rank 0.** It was created by an import and is not in the bundled list. Set its
rank in **Admin → Staff → Designations** (rank is editable in the list), or run `seed_nbte` which
ranks it from its title. `audit_designations --unranked` lists them.

---

## Data reference — what `seed_nbte` creates

### Departments (20)
The name is what HR writes in the DEPARTMT column. Existing departments are matched by name, code
or full title and left as they are (e.g. your “Human Resources”/`hr` is used for HUMAN RESOURCE).

| Name on the roll | Code | Full title |
|---|---|---|
| ES OFFICE | `es-office` | Executive Secretary's Office |
| ICT | `ict` | ICT Department |
| INT. AUD. DEPT | `int-aud-dept` | Internal Audit Department |
| PRCM | `prcm` | Procurement Department |
| COE | `coe` | Centre of Excellence |
| FINANCE | `finance` | Finance and Account Department |
| HUMAN RESOURCE | `hr` | Human Resources Department |
| SEVICOM | `sevicom` | SERVICOM Department |
| ASPD | `aspd` | Academic and Strategic Planning Department |
| PPD | `ppd` | Physical Planning & Development Department |
| GAMD | `gamd` | General Administration and Management Department |
| IRD | `ird` | Innovation and Research Department |
| TS&DD | `tsdd` | Technical Skills & Development Department |
| NTIRD | `ntird` | New TVET Institutions Registration Department |
| DPAP | `dpap` | Polytechnic and Allied Programmes Department |
| INPD | `inpd` | Inspectorate Programmes Department |
| CURR. DEPT. | `curr-dept` | Curriculum Department |
| NHSPD | `nhspd` | Nursing and Health Sciences Programmes Department |
| ZONAL OFFICE | `zonal-office` | Zonal Offices |
| LIAISON OFFICE | `liaison-office` | NBTE Liaison Office, Abuja |
The importer also recognises, and creates when it meets them: SPD (Strategy and Partnership),
ACPD, VTS&D, APRS&ICT, CONSULT, GDD, MONOTECHNIC, NIRD, PPG, and one “ZONAL OFFICE <CITY>” per
zonal office (Awka, Bauchi, Katsina, Lagos, Nasarawa, Port-Harcourt) from the section headings.

### Staff states
| Name | Meaning |
|---|---|
| Confirmed | REMARKS = CONFIRMED (also the default for a new record with no remark) |
| Probation | REMARKS = PROBATION |

Other REMARKS values in the file (e.g. “SECONDMENT TO THE BOARD”, “BPP STAFF”) are created as
staff states by the importer the first time they appear.

### States of origin (36 + FCT)
| State | Code | Zone |
|---|---|---|
| ABIA | `ab` | SE |
| ADAMAWA | `ad` | NE |
| AKWA IBOM | `ak` | SS |
| ANAMBRA | `an` | SE |
| BAUCHI | `ba` | NE |
| BAYELSA | `by` | SS |
| BENUE | `be` | NC |
| BORNO | `bo` | NE |
| CROSS RIVER | `cr` | SS |
| DELTA | `de` | SS |
| EBONYI | `eb` | SE |
| EDO | `ed` | SS |
| EKITI | `ek` | SW |
| ENUGU | `en` | SE |
| GOMBE | `go` | NE |
| IMO | `im` | SE |
| JIGAWA | `ji` | NW |
| KADUNA | `kd` | NW |
| KANO | `kn` | NW |
| KATSINA | `kt` | NW |
| KEBBI | `ke` | NW |
| KOGI | `ko` | NC |
| KWARA | `kw` | NC |
| LAGOS | `la` | SW |
| NASARAWA | `na` | NC |
| NIGER | `ni` | NC |
| OGUN | `og` | SW |
| ONDO | `on` | SW |
| OSUN | `os` | SW |
| OYO | `oy` | SW |
| PLATEAU | `pl` | NC |
| RIVERS | `ri` | SS |
| SOKOTO | `so` | NW |
| TARABA | `ta` | NE |
| YOBE | `yo` | NE |
| ZAMFARA | `za` | NW |
| FCT | `fc` | NC |
Spellings the importer maps: KAN0 → KANO, KASTINA → KATSINA, AK/IBOM and AKWA-IBOM → AKWA IBOM,
C/ RIVER → CROSS RIVER, ABUJA → FCT, NASSARAWA → NASARAWA.

### Designations (117) and the promotion ladder
Built from every spelling in the nominal roll (`staff/data/designations.json`; the other spellings
are stored as aliases so imports match them). Ranks follow the heuristic in `NOMINAL_ROLL.md`;
CONTEDISS is the range seen in the file and is used to auto-fill the grade on promotion. Tune any
of these in the admin — the seed never overwrites a value you set.

| Rank | Designation | CONTEDISS | Next step |
|---:|---|---|---|
| 100 | EXECUTIVE SECRETARY | 99 | — |
| 90 | AGT. DIRECTOR | 14 | EXECUTIVE SECRETARY |
| 90 | AGT. DIRECTOR PHARMACIST | 14 | EXECUTIVE SECRETARY |
| 90 | DIRECTOR | 15 | EXECUTIVE SECRETARY |
| 80 | AGT. DEPUTY DIRECTOR | 14 | DIRECTOR |
| 80 | DEPUTY DIRECTOR | 13–14 | DIRECTOR |
| 80 | DEPUTY DIRECTOR PHARMACIST | 14 | AGT. DIRECTOR PHARMACIST |
| 70 | CH. ACCOUNTANT | 13 | DEPUTY DIRECTOR |
| 70 | CH. HUMAN RES. OFFICER | 13 | DEPUTY DIRECTOR |
| 70 | CH. MED. LAB. TECH | 13 | DEPUTY DIRECTOR |
| 70 | CHIEF EX. OFFR(AUDIT) | 13 | DEPUTY DIRECTOR |
| 70 | CHIEF EXEC. OFFR | 12–13 | DEPUTY DIRECTOR |
| 70 | CHIEF INT. AUDITOR | 13 | DEPUTY DIRECTOR |
| 70 | CHIEF MATRON | 13 | DEPUTY DIRECTOR |
| 70 | CHIEF PROCUREMENT OFFR. | 13 | DEPUTY DIRECTOR |
| 70 | CHIEF PROG. OFFR | 13 | DEPUTY DIRECTOR |
| 70 | CHIEF TECH. OFFR | 12–13 | DEPUTY DIRECTOR |
| 70 | CHIEF. CONF.SEC. | 13 | DEPUTY DIRECTOR |
| 70 | CHIEF. PLNG. OFFR. | 13 | DEPUTY DIRECTOR |
| 70 | CHIEF. STAT. OFFR | 12–13 | DEPUTY DIRECTOR |
| 65 | ASSIT. CH. PLNG. OFFR. | 12–13 | CHIEF. PLNG. OFFR. |
| 65 | ASSIT. CHIEF. CONF.SEC. | 11 | CHIEF. CONF.SEC. |
| 65 | ASST. CH. . EXEC. OFFR. | 11–12 | CHIEF EXEC. OFFR |
| 65 | ASST. CH. BUILDER | 12 | CHIEF MATRON |
| 65 | ASST. CH. DATA PROSC. OFF. | 11–12 | CHIEF MATRON |
| 65 | ASST. CH. EXE. OFFICER (LIBRARY) | 12 | CHIEF MATRON |
| 65 | ASST. CH. HUMAN RES. OFFICER | 12 | CH. HUMAN RES. OFFICER |
| 65 | ASST. CH. MED. LAB. TECH | 12 | CH. MED. LAB. TECH |
| 65 | ASST. CH. PROG. OFFR. | 12 | CHIEF PROG. OFFR |
| 65 | ASST. CH. PROGS. | 12 | CHIEF MATRON |
| 65 | ASST. CH. STORES OFFR. | 11–13 | CHIEF MATRON |
| 65 | ASST. CH. TECH. OFFR. I | 12 | CHIEF TECH. OFFR |
| 65 | ASST. CH. TECH. OFFR.(Arc.) I | 11–12 | CHIEF MATRON |
| 65 | ASST.CH. ACCOUNTANT | 12 | CH. ACCOUNTANT |
| 65 | ASST.CH. INT. AUDIT | 12 | CHIEF MATRON |
| 65 | ASST.CH. LEGAL OFF | 12 | CHIEF MATRON |
| 65 | Asst. Ch. PROCUREMENT OFFICER | 12 | CHIEF PROCUREMENT OFFR. |
| 65 | CHIEF SECRETARIAL ASST. | 8 | CHIEF MATRON |
| 61 | PRIN . EXEC. OFFR. I | 11 | ASST. CH. . EXEC. OFFR. |
| 61 | PRIN. DENTAL. TECH. I | 9 | ASST. CH. PROGS. |
| 60 | Consul./ Prin. Medical Officer II | 13 | PRIN . EXEC. OFFR. I |
| 60 | PRIN. ACCOUNTANT | 11 | ASST.CH. ACCOUNTANT |
| 60 | PRIN. CONF. SEC. | 9 | ASSIT. CHIEF. CONF.SEC. |
| 60 | PRIN. DATA PROSC. OFF. | 9 | ASST. CH. DATA PROSC. OFF. |
| 60 | PRIN. EXE. OFF | 9 | PRIN . EXEC. OFFR. I |
| 60 | PRIN. EXE. OFF. AUDIT | 9–11 | CHIEF EX. OFFR(AUDIT) |
| 60 | PRIN. EXEC. OFFR.(ACCTS) | 9 | PRIN . EXEC. OFFR. I |
| 60 | PRIN. EXEC. OFFRICER II | 9 | PRIN . EXEC. OFFR. I |
| 60 | PRIN. HUMAN RES. OFFICER | 11 | ASST. CH. HUMAN RES. OFFICER |
| 60 | PRIN. INT. AUDIT | 11 | ASST.CH. INT. AUDIT |
| 60 | PRIN. LEGAL OFFR . | 11 | ASST.CH. LEGAL OFF |
| 60 | PRIN. PHAM TECH | 9–11 | PRIN . EXEC. OFFR. I |
| 60 | PRIN. PLNG. OFFR. | 11 | ASSIT. CH. PLNG. OFFR. |
| 60 | PRIN. PROG. OFFR. | 11 | ASST. CH. PROG. OFFR. |
| 60 | PRIN. TECH. OFFR | 9 | ASST. CH. TECH. OFFR. I |
| 60 | PRIN. TECH. OFFRICER II | 9 | ASST. CH. TECH. OFFR. I |
| 58 | SENIOR PROG. OFFR. I | 8 | PRIN. PROG. OFFR. |
| 57 | SENIOR AUDITOR | 9 | SENIOR PROG. OFFR. I |
| 57 | SENIOR TECH. OFFR. | 8 | PRIN. TECH. OFFR |
| 57 | SNR. .EXEC. OFFICER | 8–9 | PRIN. EXE. OFF |
| 57 | SNR. ACCOUNTANT | 9–11 | PRIN. ACCOUNTANT |
| 57 | SNR. CONF. SEC. | 8 | PRIN. CONF. SEC. |
| 57 | SNR. DENTAL. TECH. | 7 | PRIN. DENTAL. TECH. I |
| 57 | SNR. HUMAN RES. OFFR. | 9 | PRIN. HUMAN RES. OFFICER |
| 57 | SNR. INT. AUDIT | 9 | PRIN. INT. AUDIT |
| 57 | SNR. LEGAL OFF | 9 | PRIN. LEGAL OFFR . |
| 57 | SNR. NUR. OFFR. | 8 | SENIOR PROG. OFFR. I |
| 57 | SNR. PLNG. OFFR. | 9 | PRIN. PLNG. OFFR. |
| 57 | SNR. PROC. OFF . | 9 | Asst. Ch. PROCUREMENT OFFICER |
| 57 | SNR. PROG. OFFR. | 9–11 | SENIOR PROG. OFFR. I |
| 57 | SNR. STORE OFFR (ACCOUNT) | 8 | SENIOR PROG. OFFR. I |
| 56 | HIGHER EXEC. OFFR. I | 7 | SNR. .EXEC. OFFICER |
| 55 | H.T.O | 7 | SENIOR TECH. OFFR. |
| 55 | HIGHER EXE. OFF (ACCTS.) | 7–8 | PRIN. EXEC. OFFR.(ACCTS) |
| 55 | HIGHER EXECUTIVE OFFICER AUDIT | 7 | PRIN. EXE. OFF. AUDIT |
| 55 | HIGHER EXECUTIVE OFFR. | 7 | HIGHER EXEC. OFFR. I |
| 55 | HTO ESTATE | 7 | HIGHER EXEC. OFFR. I |
| 40 | ACCOUNTANT I | 8 | SNR. ACCOUNTANT |
| 40 | AUDITOR I | 8 | SENIOR AUDITOR |
| 40 | CONF. SEC. I | 7 | SNR. CONF. SEC. |
| 40 | HUMAN RES.OFF. I | 7–8 | SNR. HUMAN RES. OFFR. |
| 40 | ICT OFF. I | 8 | H.T.O |
| 40 | LEGAL OFFR I. | 8 | SNR. LEGAL OFF |
| 40 | PLANG. OFF. I | 8 | SNR. PLNG. OFFR. |
| 40 | PROC. OFF. I | 8–9 | SNR. PROC. OFF . |
| 40 | PROG. OFF. I | 7–8 | SNR. PROG. OFFR. |
| 40 | STAFF MIDWIFE I | 8 | H.T.O |
| 35 | ACCOUNTANT II | 7 | ACCOUNTANT I |
| 35 | HUMAN RES. OFFR. II | 7 | HUMAN RES.OFF. I |
| 35 | ICT OFF. II | 7 | ICT OFF. I |
| 35 | NUR. OFFR. II | 6–7 | SNR. NUR. OFFR. |
| 35 | PLANNING OFF. II | 7 | PLANG. OFF. I |
| 35 | PROC. OFF. II | 7 | PROC. OFF. I |
| 35 | PROG. OFF. II | 7–8 | PROG. OFF. I |
| 35 | TECH. OFF. II | 9 | H.T.O |
| 30 | DATA PRO. OFF. | 7 | PRIN. DATA PROSC. OFF. |
| 25 | EX. OFFR(PERS) | 6 | DATA PRO. OFF. |
| 25 | EXECUTIVE OFFICER | 5–7 | HIGHER EXECUTIVE OFFR. |
| 24 | CH.CL.OFFR (PERS) | 6 | EX. OFFR(PERS) |
| 24 | CHIEF CLERICAL OFF. | 6 | EX. OFFR(PERS) |
| 22 | PRIN. MEDICAL I | 6 | CH.CL.OFFR (PERS) |
| 21 | SENIOR CLERICAL OFF. | 5 | CHIEF CLERICAL OFF. |
| 21 | SN. SEC. ASSISTANT | 6 | PRIN. MEDICAL I |
| 20 | CH. DRIVER/MECH. | 6 | SN. SEC. ASSISTANT |
| 20 | CHIEF FIREMAN | 6 | SN. SEC. ASSISTANT |
| 20 | CHIEF HEAVY DUTY DRIVER | 6 | SN. SEC. ASSISTANT |
| 20 | CLERICAL OFFICER | 4 | SENIOR CLERICAL OFF. |
| 17 | CONF. SEC. II | 6 | CONF. SEC. I |
| 16 | TECH. OFF. | 6 | TECH. OFF. II |
| 15 | ASST. TECH. OFF. | 5 | TECH. OFF. |
| 15 | ASST.EXE. OFFR. | 5 | EXECUTIVE OFFICER |
| 15 | SEN. FOREMAN(CARPT) | 6 | TECH. OFF. |
| 15 | SENIOR DRIVER | 5 | TECH. OFF. |
| 15 | SNR. FOREMAN (ELECT.) | 6 | TECH. OFF. |
| 15 | SNR. FOREMAN (PLUMB.) | 6 | TECH. OFF. |
| 10 | MOTOR DRIVER/ MECH | 5 | SENIOR DRIVER |
| 0 | HUMAN | 7 | — |