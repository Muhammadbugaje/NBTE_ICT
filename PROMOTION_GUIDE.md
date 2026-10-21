# Promotion guide (for HR)

You need the **Promote staff** permission. Menu: **People → Promotions**.

## Promote one or several people

1. Filter the list by **Department**, **Office**, **Current designation**, or search a name / file no.
2. Tick the people (or **Select page**). The bar shows how many are selected.
3. Click **Promote selected…**. In the dialog:
   * **New designation** — only posts ranked above the current one are listed. If you ticked people on
     different designations, an amber note lists them.
   * **Effective date** — today by default. It becomes their *date of present appointment*.
   * **New grade level** — filled from the designation's CONTEDISS range (e.g. CONTEDISS 9). If the
     post has no range, the grade stays as it is and you are told so.
   * **Reason** — recommended, e.g. “2026 promotion exercise, Board approval 30 Sept 2026”.
   * **Notify affected staff** — sends each person an in-app message and their department head a
     summary. On by default for one person, off for cascades.
4. Click **Preview**. The right-hand side lists every change: *PROG. OFF. II → PROG. OFF. I, GL 7 → 8*.
5. Click **Confirm promotion**. All changes are saved together.

From a staff member's page you can use **Promote** for just that person; the same dialog opens.
Their page shows everything in the **Staff file** timeline.

## Transfers (posting to another department or zonal office)

On the staff member's page click **Transfer** (permission *Can transfer staff*): choose the new
department / zonal office and/or office, the effective date and the reason (posting reference), and
whether to notify them. The move appears in their **Staff file**. Department or office changes
made by editing the record or by an import are recorded there too.

## Cascade — worked examples

Tick **Also cascade** to promote, with the people you ticked, everyone else who:
**is on the same designation**, **in the same department**, and **was appointed to it later**
than the ticked person. They move **one step** up the ladder (or, with the second option, to the
same new designation). The dialog shows the live count; the preview lists them marked *cascade*.

*Example 1.* In DPAP, Amina (PROG. OFF. II since 2019) is promoted to PROG. OFF. I with cascade.
Bello (PROG. OFF. II since 2021, DPAP) moves too — one step, to PROG. OFF. I. Chidi (PROG. OFF. II
since 2017, DPAP) does **not** (appointed earlier). Dauda (PROG. OFF. II since 2022, **ICT**) does
**not** (other department).

*Example 2.* Amina jumps two steps, PROG. OFF. II → SNR. PROG. OFFR. With cascade “one step”, Bello
still goes only to PROG. OFF. I; with “up to the same new designation”, he goes to SNR. PROG. OFFR.

*Example 3.* Someone with no *date of present appointment* is ticked: nobody cascades with them
(the preview says so). Fill the date on their record first.

Each cascaded promotion is recorded with a link to the ticked person's promotion.

## History and rollback

**Promotions → History** lists every promotion and rollback, filterable by date range, department
and approver. To undo one: **Roll back** → give a reason (required) → **Roll back**. The person's
designation, grade and date of present appointment return to what they were, and a *Rollback* entry
is added — nothing is deleted. A rollback is refused if the person has been promoted again since
(roll back the later one first) or if it was already rolled back. Cascaded promotions are rolled
back one by one.

## Tuning the ladder

Ranks, CONTEDISS ranges and the normal next step live in **Admin → Staff → Designations** (rank,
active and order are editable in the list). `python manage.py audit_designations` prints the table.
