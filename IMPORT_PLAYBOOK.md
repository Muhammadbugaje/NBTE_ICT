# Import playbook — loading the nominal roll (for HR)

You need the **Import nominal roll** permission. If you don't see **Administration → Staff import
& export** in the menu, ask the ICT administrator to give it to your role.

## A. Import the nominal roll

1. Open **Administration → Staff import & export**. You land on the **Import** tab. (Administration
   → Import / Export → *Staff* opens the same page.)
2. Drag the Excel file onto the box, or click **Choose the Excel file**. Use the file exactly as HR
   keeps it — title rows, department headings, the old sheets and the empty Sheet1–3 are fine.
   Only `.xlsx` workbooks can be read (an old `.xls`: open it in Excel → *Save As* → *Excel Workbook*).
3. Click **Upload and preview**. Nothing is saved yet — this is a *dry run*.
4. Read the preview:
   * **Totals** — how many people would be added (New), changed (Updated), already up to date
     (Unchanged) and skipped.
   * **Sheets to import** — every sheet with data is ticked. Older sheets are applied first and the
     newest wins, so ticking the old September 2022 sheet only adds people who appear on no newer
     sheet. **Untick a sheet** to leave it out (see *Not on the latest full roll* below).
   * **Upsert mode** — *Update existing* (normal), *Skip existing* (only add new people), or *Fail on
     any duplicate* (refuse the whole import if anyone in the file is already in the system).
   * **Sheet by sheet** — the numbers per sheet. *Superseded* = rows a newer sheet overrides.
   * The amber panels — warnings (explained in section C).
5. If you changed a tick or the mode, click **Dry-run again** to refresh the numbers (the Confirm
   button waits until you do).
6. Click **Confirm import**. Everything is saved in one go — or, if something goes wrong, nothing
   is. You are taken to the **History** tab.
7. On **History**, click **Report** to download the full report for your records.

Running the same file again is safe: people already up to date are counted as *Unchanged* and
nothing is rewritten. **Empty cells never erase** what is already saved — to clear a value, edit the
staff record.

Command line (ICT): `python manage.py import_nominal_roll "<file>.xlsx" --dry-run`, then without
`--dry-run`. Options: `--sheet "NEW STAFF 2026"` (repeatable), `--mode skip|fail`. The report is
written next to the file as `<file>.xlsx.report.txt`.

## A2. Give everyone a sign-in account

On the preview, tick **Create a sign-in account for everyone in this import who doesn't have one**
(you need the *Can add user* permission).

* **Add an EMAIL column** to the file (the template has one, after QUALIFICATION (S)). The email
  becomes the person's **sign-in name**, they get a **welcome email** with their temporary password
  (leave “Email each person their sign-in details” ticked), and they can use *Forgot password*.
* People with no email sign in with their **file number** for now. When you add their email later
  (edit their record, or import again with the EMAIL column filled), it becomes their sign-in
  automatically. Staff can also change their email on *My profile*.
* Choose the temporary password: **Surname + file number** (e.g. `jabir1413` — easy, but guessable
  by anyone holding the roll, so ask people to sign in and change it quickly), **one starter
  password** you type, or **random** (download the *Sign-in details* sheet once from **History**; it is
  deleted after the download). Everyone must change the password the first time they sign in.
* The preview shows how many accounts will be made and how many of them have an email.

Before the first big import, ask ICT to run `python manage.py check_email --send --to <your email>`
to confirm emails really go out (see `SETUP.md` → *Email*).

## B. How people are matched

1. By **FILE NO**. 2. If the FILE NO isn't in the system: by surname + other name + date of birth.
3. Then by surname + other name, if exactly one existing record has that name and its FILE NO is not
in the file. 4. By EMAIL, if the file has one. 5. Someone who already has a sign-in account but no
file number yet (record number starting `USR-`) is **merged** when the surname matches and an other
name is shared — the preview lists these under *Merged with existing account records*; check them. Otherwise the person is added as new. A row with no FILE NO can only update an
existing record with the same name — it is never added.

## C. What each warning means and what to do

| Warning | Meaning | What to do |
|---|---|---|
| `D_… '30-3-234' 3-digit year … read as 2024` | A typo in the year; the stray digit was dropped. | Check the date; correct the cell if 2024 is wrong. |
| `D_… '5/13/96' read as month/day` | Written month-first (13 can't be a month). | Usually right. Retype as 13-May-96 to be sure. |
| `D_… 'SECONDMENT TO THE BOARD' is not a date` | Text in a date column. | Move the text to REMARKS; leave the date empty or put the real date. |
| `D_BIRTH 2026-10-01 is not a plausible birth date — not stored` | Birth date makes the person under 15 (or born before 1930). | Put the real date of birth in the file, or edit the staff record. |
| `D_… '…' is not a valid date` | Unreadable (e.g. 31 Feb). | Correct the cell. |
| `'05-03-92' is ambiguous; read as day-month` (Notes) | Could be 5 March or 3 May — read day first, the NBTE convention. | Nothing, unless it was meant month-first. |
| `Duplicate FILE NO 1487 …` | Two different people share a FILE NO in one sheet. The first is imported, the second skipped. | Give the second person their correct FILE NO and import again. |
| `FILE NO … is X on 'sheet A' but Y here` | Same FILE NO, different person on two sheets. The newer sheet is used. | Check which is right; fix the older sheet or the record. |
| `FILE NO is blank / '+' — matched by name to FILE NO 1224` | Row without a usable FILE NO, matched to the same name elsewhere. | Fill in the FILE NO. |
| `no FILE NO and no existing record with this name — row skipped` | Can't create someone without a FILE NO. | Fill in the FILE NO. |
| `CONTEDISS/CONHESS holds 'PRIN. PROG. OFFR.' and DEPARTMT holds '11' — read as …` | The row slipped one cell to the right; it was read back into place. | Fix the row in the file so it stays right next time. |
| `CONTEDISS/CONHESS '…' is not a level — ignored` | Text where the grade should be. | Put the number (e.g. 9). |
| `DESIGNATION '15' is not a post title — ignored` | A number where the designation should be. | Put the post title. |
| `SEX '…' is not M or F — ignored` | | Use M or F. |
| `ST_ORIGIN '…' is not a Nigerian state — kept as written` (Notes) | Unknown spelling; a new state-of-origin entry is created. | Correct the spelling; merge in the admin if needed. |
| `FILE NO … belongs to a deleted record — restore it in the admin first` | That person was deleted in the system. | Restore them in Admin → Staff, then import again. |
| **Not on the latest full roll (82)** | People who appear only on older sheets (e.g. Sept 2022) — likely retired, transferred or left. | Untick the older sheets if you don't want them, or import them and set their state (e.g. Retired) afterwards. |

“Import stopped — nothing was saved” means an unexpected error; the message names the sheet, row
and FILE NO. Fix that row and upload again.

## D. The 12 date warnings in the October 2026 workbook

They are 5 distinct problems repeated on several sheets. They can be **ignored** (the import
already reads them sensibly), but fixing them in the master file is better:

| Who | Cell | In the file | Read as | Fix |
|---|---|---|---|---|
| SA'AD ABDULMAJID | D_FAPPMT | `30-3-234` | 30 Mar 2024 | type `30-Mar-2024` (3 sheets) |
| SANI RABI | D_COF | `5/13/96` | 13 May 1996 | type `13-May-96` (3 sheets) |
| BADAMASI MOHAMMED IIDRIS (1583) | D_BIRTH | 1 Oct 2026 | not stored | put the real date of birth (3 sheets) |
| USMAN MOHAMMED TATA | D_COF | `]4/24/92` (Sept 2022 sheet) | 24 Apr 1992 | remove the `]` |
| GWARZO SULEIMAN SA'AD | D_COF | `SECONDMENT TO THE BOARD` (Sept 2022 sheet) | empty | move the text to REMARKS |
| YALWAJI SALAMATU SALEH | D_BIRTH | 2 Dec 2019 (Sept 2022 sheet, shifted row) | not stored | correct the row |

The other date spellings in the file (`7th Mar. 58`, `2-0CT-26`, `13- Aprl-2018`, `1st - MAY- 88`…)
are all read correctly and produce no warning. The brief anticipated 14 typos; this workbook gives
12 warnings for the 6 cases above.

## E. Use the blank template

1. **Staff import & export → Template → Download blank template**.
2. Sheet **NOMINAL ROLL**: one row per person from row 3. Row 2 (grey) is an example and is skipped.
   Hover a column heading in Excel for what to type. DEPARTMT, DESIGNATION, ST_ORIGIN, REMARKS and
   SEX offer drop-downs (from the **REFERENCE** sheet — today's lists). Typing a new designation is
   allowed; Excel asks you to confirm.
3. Dates: best as real Excel dates (format `d-mmm-yy`); `7-Mar-90`, `07-03-90` also work.
4. Save as `.xlsx` and import it as in section A.

## F. Export

**Staff import & export → Export**: *Full nominal roll* (optionally one department) or *One sheet per
department*. Same 17 columns and order as the official file, sorted by department, seniority and
surname; dates are real Excel dates shown like `7-Mar-58`. You only get the staff you are allowed to
see. Every export is written to the audit log. An export can be edited and imported again.
