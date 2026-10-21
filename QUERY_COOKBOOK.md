# Query cookbook — ready-made staff queries

Menu: **People → Advanced query**. Add conditions, choose **All conditions (AND)** or **Any
condition (OR)**, then **Preview results**. **Save query** keeps it (tick *Share with everyone* if
you have the permission); **Export .xlsx** downloads the result as a nominal roll.

Dates can be relative so a saved query stays current: `today`, `today-12m` (12 months ago),
`today-30d`, `today+60d`, `-1y`. Lists use commas: `ICT, FINANCE`.
Each recipe below can also be opened by URL (append to `/staff/query/?`).

| # | Query | Match | Conditions |
|---|---|---|---|
| 1 | **On probation for over 12 months** | AND | Staff state *equals* Probation · Date joined the Board *<* `today-12m` |
| 2 | **Directors and above, by department** | AND | Designation rank *≥* 90 — export it: the file is grouped by department |
| 3 | **Due for confirmation** (2+ years in service, not confirmed) | AND | Date of confirmation *is empty* · Date joined the Board *≤* `today-24m` |
| 4 | **Due for promotion** (3+ years in post, below Director) | AND | Date of present appointment *≤* `today-3y` · Designation rank *<* 80 |
| 5 | **Retiring within a year** (60 years of age or 35 years of service) | OR | Date of birth *≤* `today-59y` · Date of first appointment *≤* `today-34y` |
| 6 | **Women in ICT** | AND | Department *equals* ICT · Sex *equals* F |
| 7 | **Grade levels 12–14** | AND | Grade level *between* 12 and 14 |
| 8 | **Staff from the North-West states** | AND | State of origin *is one of* `JIGAWA, KADUNA, KANO, KATSINA, KEBBI, SOKOTO, ZAMFARA` |
| 9 | **Records missing data** | OR | Date of birth *is empty* · Designation *is empty* · State of origin *is empty* |
| 10 | **New recruits this year** | AND | Date joined the Board *≥* `2026-01-01` |
| 11 | **Zonal office staff** | AND | Department *starts with* ZONAL OFFICE |
| 12 | **Programme officers (any grade)** | AND | Designation *contains* PROG |

URL forms (examples):
* #1 `field=state&op=eq&value=Probation&field=date_joined_board&op=lt&value=today-12m&logic=AND`
* #5 `field=date_of_birth&op=lte&value=today-59y&field=date_first_appointment&op=lte&value=today-34y&logic=OR`
* #7 `field=grade_level&op=between&value=12&value2=14`

Notes
* A department head only ever sees their own department — the same query shows each user their
  part. Other departments appear greyed out in the Department picker.
* “Designation rank” comes from the ladder (Executive Secretary 100, Director 90, Deputy Director 80,
  Chief 70…) — see `SETUP.md`.
* “Is one of” on names ignores letter case; “equals” also ignores case.
