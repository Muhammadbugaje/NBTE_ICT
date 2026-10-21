# Migrations added in the nominal-roll release

All in the `staff` app. No shipped migration was edited. Each step reverses on its own.

| Migration | Forward | Reverse | Reversible |
|---|---|---|---|
| `0007_nominal_roll_models` | Creates `StateOfOrigin`, `Designation`, `Promotion`, `ImportSession`, `SavedQuery`. Adds to `Staff`: `gender`, `state_of_origin`, `lga`, `date_of_birth`, `date_first_appointment`, `date_joined_board`, `date_present_appointment`, `date_confirmation`, `qualifications`, a temporary FK `designation_fk` and a temporary text `designation_legacy`. Adds help text to `grade_level`, `birth_month`, `birth_day`; adds the 5 new permissions. | Drops all of the above. | Yes |
| `0008_designation_fk_data` (RunPython) | For every staff row (including soft-deleted): copies the text `designation` into `designation_legacy`; creates one `Designation` per distinct non-empty value (rank 0, unique code) or reuses one with the same name; points `designation_fk` at it. Idempotent. | Copies `designation_legacy` back into the text `designation` (exact original values) and clears `designation_fk`. | Yes |
| `0009_designation_fk_swap` | Removes the text column `designation`; renames `designation_fk` → `designation`. | Re-adds the text column (blank), renames the FK back to `designation_fk` (0008's reverse then refills the text). | Yes |
| `0011_staff_file_transfers_accounts` | Creates `StaffTransfer` (staff, from/to department, from/to office, effective date, reason, source, recorded by). Adds `ImportSession.accounts_created` and `ImportSession.credentials_file` (restricted storage, one-time download). Adds the `staff.transfer_staff` permission. | Drops the model and the two fields; removes the permission. | Yes |
| `0010_remove_designation_legacy` | Removes the temporary `designation_legacy`. | Re-adds it and refills it from the designation FK's name, so 0009/0008 can reverse. | Yes |

Verified (see `FINAL_CHECKLIST.md`): on a fresh SQLite database and on a copy of the shipped
`db.sqlite3` — migrate forward; reverse to 0009, 0008, 0007, 0006 one at a time; migrate forward
again. On the copy, staff `ICT-001`'s designation “Senior Officer” is identical after the round
trip. `makemigrations --check` reports no missing migrations.

Command: `python manage.py migrate` (forward) · `python manage.py migrate staff 0006` (fully back).

## messaging

| Migration | Forward | Reverse | Reversible |
|---|---|---|---|
| `0007_memo_approval_skips` | `MemoApproval`: adds `step_name` (filled from the step); `actor` becomes nullable (recorded skips have none); adds the `skip` decision; `step` becomes `SET_NULL` instead of `PROTECT`, so a route step with history can be deleted. | Deletes the rows the old schema cannot hold (skips, decisions on deleted steps), then restores the old columns. | Yes |

Verified on a copy of the shipped `db.sqlite3` (with the demo memos): `migrate messaging 0006`, then `migrate`.
