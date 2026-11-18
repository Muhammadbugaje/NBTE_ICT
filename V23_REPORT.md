# NBTE Digital QA v2.3: Phases 2–4 and the QA pages redesign

This release builds on Phase 1 (`PHASE1_REPORT.md`). It turns the owner's ten answers into
working behaviour and redesigns the QA pages. Each item lists where it lives in the code and
the test that checks it.

## The owner's decisions, as built

| # | Decision | What the system does now | Where | Test |
|---|---|---|---|---|
| 1 | "Cleared for QA" after SOP-01; "Approved" only after the ES's final approval in SOP-03 | Clearing the last self-study sets `Institution.cleared_for_qa_at` and shows **Cleared for QA**, then opens the first QA request. The old Director-approve + ES-confirm step is gone. The institution becomes **Approved** when the ES's final decision on its first SOP-03 case is applied (`onboarding_v4.finalise_institution`, stage 17). Later cases are ordinary requests. The portal journey says "Cleared for QA". | `institutions/models.py`, `accreditation/onboarding_linear.py`, `qa_core/portal_journey.py`, migration `institutions/0025` (backfill) | `test_linear_onboarding.py`, `test_v23_features::test_cleared_for_qa_label…`, `test_v23_redesign::test_portal_journey_says_cleared_for_qa` |
| 2 | Division Director checks the visit team and dates and owns the stage 4 personnel confirmation; Zonal Director is consulted and may lead the team; advisory only | The owning Director picks a Division Director on the case Overview. That person can act on stage 4 (personnel) and stage 9 (visit team and dates); the Director keeps the right to act. Zonal Directors of the institution's zone are notified when a visit is saved. There is no extra approval step. | `accreditation/case_services.py` (`DIVISION_DIRECTOR_STAGES`, `set_division_director`, `_consult_zonal_directors`), migration `accreditation/0031` | `test_v23_features::test_only_the_owning_director…` |
| 3 | Old version locked and readable; new copy opens; dated notes allowed | A rollback freezes each affected record (report, cost sheet, decisions, personnel) as an `ArtifactVersion`, which can never be edited. The work is copied forward with the version number raised. Staff who can see the case can add dated notes (`ArtifactAddendum`). See **Reports → Version history** on any case. | `accreditation/versions.py`, `case_changes.py`, migration `0031` | `test_v23_features::test_frozen_version…`, `…test_version_notes_are_staff_only` |
| 4 | Chairman sees the votes but his form starts empty; ES decides blind; same for every approver | Every decision form starts empty ("Choose…"). The Chairman sees the committee's vote tally. The ES and other approvers see no earlier decision until they save their own. | `accreditation/decisions.py` (`BLIND_STAGES`, `reveal_earlier`, `_vote_tally`) | `test_phase8_decisions.py`, `test_sop03.py` |
| 5 | HND without an accredited ND: warning plus a required justification, never a block | The new-request form shows the warning for each such HND and requires a note of at least 10 characters, saved on the request row. Requests the system creates itself (the onboarding auto-start) are never blocked. | `accreditation/qa_services.py` (`nd_prerequisite_warning`, `create_request`) | `test_v23_features::test_hnd_without_accredited_nd…`, `…test_portal_request_must_explain…` |
| 6 | Minimum running period: admin-set, flagged as a policy item | Setting `qa.programme.min_running_months_before_accreditation`, default **24 months**, with **0 = off**. The setting's help text marks it as an NBTE policy item to confirm. The eligibility message gives the date accreditation opens. | `qa_core/settings_catalogue.py`, `qa_services.accreditation_opens` | `test_v23_features::test_new_programme_waits…` |
| 7 | Files on Cloudflare R2 | Set `FILE_STORAGE=r2` to put both upload stores on R2, using two buckets so restricted files stay private. Without it, files stay in the local folders. Setup and moving existing files are covered in `deploy/R2_STORAGE.md`. | `nbte_ict/settings/base.py`, `core/storage.py`, `requirements.txt` (`django-storages[s3]`) | `test_v23_redesign::test_r2_storage_switch…` (3 tests) |
| 8 | Render and Aiven regions | **Action for you:** open the Render dashboard (service → Settings → Region) and the Aiven console (service → Overview → Cloud). If they differ, move one so both are in the same region. | — | — |
| 9 | Keep the platform-marker cryptography split | Kept as is in `requirements.txt`. | — | — |
| 10 | Remove the CQAC shared vote for good | The setting, its seed and its page text are removed. Migration `0030` deletes the stored value. Every member votes separately. | `decisions.py`, `settings_catalogue.py`, `seed_startup.py` | `test_phase8_ui::test_cqac_shared_vote_setting_is_gone`, `test_phase8_decisions::test_cqac_members_always_vote_individually` |

## Other improvements in this release

- **Devices declared or found not working.** The SSQ, the snapshot and the VVT-001 register record working and not-working counts. A device that was declared faulty is no longer counted as falsified. Each case has a **Devices register**.
- **Letters fit on one page,** with the NBTE crest faintly behind the text (setting `qa.documents.crest_watermark`) and a QR code that checks the letter against the issued-documents registry. A re-issued letter gets its own registry record.
- **Backups.** Media is stored as `.zip`, keeping folder paths, and is restored safely: a path that tries to escape the media folder is skipped. Every backup now includes `data.xlsx`, and the history has a "Download…" menu.
- **Tasks.** QA tasks now have **Accept & proceed to QA**, which accepts the task and opens the case, plus **Return**, **Decline** and **Reject**, each with a reason.
- **Team Lead's compiled report starts pre-filled.** It merges the accepted attendee reports section by section, with each line naming its author, and takes the recommendation most attendees chose. Skipped and unreviewed contributions are left out, and the Team Lead's own saved text is never overwritten.
- **Workload card for Directors.** QA home shows each person's open QA tasks and how many are overdue, plus **segregation-of-duties conflicts**: one person holding two of the desk, Audit and Finance roles on a case.
- **Security fix.** Holding the case permissions, for example through the "QA Desk" group, used to show *every* department's cases and letters. It now gives access to the case screens only within the person's own scope.
- **Portal sign-in.** `/login/?username=…` links from invitation e-mails now pre-fill the e-mail box.
- **Fee formula preview.** The preview now knows the `falsified` variable.
- **Plain words.** Workflow history reads "Payment confirmed · now: Fee confirmed" rather than raw codes.

### Fixes found while testing this release

These were already broken before v2.3. The test run found them, and they are fixed here.

- **Mobile API server errors.** The staff API's URL namespace had been renamed from `api` to `api_staff` to silence a warning. After that, every memo PDF link, chat attachment and announcement attachment failed with a server error. The namespace is back to `api` and the warning is silenced, with the reason written in the settings.
- **Settings that would not save on PostgreSQL.** Five settings had descriptions longer than the 255-character column, so they would fail on the production database (SQLite does not check the length). The descriptions are now shorter.
- **Fresh installs gave the ES role no baseline permissions.** `seed_roles` did not recognise four staff permissions added by migrations, so it treated the ES role as already set up and skipped it.
- **The portal's "Forgot password" page crashed** when opened without `?email=` under strict template checking. It now fills the box from the link safely.
- **Change requests** (the ICT lead's list and detail) now look up records through the service layer, which the scoping audit requires.
- **Brand colours.** Five stylesheets hard-coded the NBTE green, so the appearance palettes could not recolour them. They now use the theme colour token. The v2.3 layer is built into the "finish" CSS bundle (`manage.py build_css`), so pages still load at most five stylesheets.
- `.env.example` (every variable the code reads, including the R2 ones) and `.gitignore` were missing from the upload. They are included again. **If your repository already has them, compare before you replace them.**

## The QA pages redesign

All new styling is in one layer, `static/css/qa-v23.css`, built into the end of the "finish" bundle
(`core/css_bundles.py`; run `python manage.py build_css` after editing it). To undo the visual
changes, remove that one line and rebuild.

- **Case page: 11 tabs become 7 sections.** The sections are Overview, Money, Team & tasks, Visit, Reports, Decision and History. A section with two views shows a small second row, for example Money → Cost sheet / Fund release. Every old `?tab=` link still works. A dot marks the section where it's your turn.
- **Overview is about half the height.** Finished stages fold into one line ("Stages 1–14 done"), and the rarely used "send back to an earlier stage" form sits behind a fold. The Division Director row and picker are on the same card as the people on the case.
- **Decision briefing.** Each programme's evidence opens on demand. If there is only one programme, it is open already.
- **Case Board.** Columns share the width. Empty columns stay narrow, cards no longer overflow, and on a phone the columns stack.
- **Set-up prompts** (profile, signature, bank details) are one slim line each and sit side by side. On a phone they shrink to a title and a button.
- **Request detail.** Facts are listed one per line in the side column, and the history uses plain words.
- **Empty states** sit in the middle of their area. **Portal sidebar** groups with no links are hidden (this affected the professional body portal).
- Motion respects "reduce motion". Every new control has a visible keyboard focus.

## Migrations

| Migration | Forward | Reverse |
|---|---|---|
| `accreditation/0030_remove_cqac_shared_mode` | Deletes the `qa.cqac.recommendation_mode` setting row | No-op |
| `accreditation/0031_v23_versions_division_director` | `ArtifactVersion`, `ArtifactAddendum`, `QACase.division_director`, `QARequestProgramme.prerequisite_note` | Drops them |
| `accreditation/0032_v23_visit_finding_not_working` | `VisitFinding.declared_not_working`, `found_not_working` | Drops them |
| `institutions/0025_institution_cleared_for_qa_at` | Adds the field and fills it for institutions already past SOP-01 | Drops it |

These were checked on a copy of the demo database by migrating back to `accreditation 0029` and
`institutions 0024` and then forward again. `makemigrations --check` reports no changes.

## Test results

**2,485 tests pass.** The 144 that still fail were all failing before v2.3: each was checked
against the original upload, or appears in Phase 1's baseline list, and fails there in the same way.
None touches the screens changed in this release. Along the way, 51 tests that were already failing
before v2.3 were fixed.

`docs/reports/v23_test_results.md` has the details: the new tests, the old tests updated for the
owner's decisions (each with its reason), the pre-existing failures that were fixed, and the ones
still failing, grouped by app with the likely cause.

## How to try it

```
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Sign in with the demo accounts (password `NBTE-Demo#2026`):

- `qa.director@demo.nbte.local` for QA home, the workload card, a case and the Case Board.
- `qa.cqac_chair@demo.nbte.local` for the Chairman's decision form, which starts empty and shows the votes.
- `cpn.registrar@demo.nbte.local` on the QA portal for the professional body portal.
