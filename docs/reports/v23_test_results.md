# v2.3 test results

Run date: 12 November 2026. Every app was run with `pytest -p no:xdist` against SQLite, using the test settings in `conftest.py`.

## Totals

- **2,485 tests pass.** That is 2,434 in the full run, plus 51 that failed there and pass after the fixes below (each one re-run).
- **144 tests still fail, all of them already failing before v2.3.** Each one was also run against the original upload (`untitled_folder.zip`), or appears in Phase 1's baseline list (`phase1_preexisting_failures.txt`), and fails there for the same reason. None was caused by this release.

## New tests in this release

- `accreditation/tests/test_v23_features.py`: frozen versions and dated notes; notes are staff-only; the Cleared for QA label; the HND warning and required justification; the minimum running period; the Division Director; devices declared or found not working; one-page letters with the crest; ZIP media backups (paths kept, no escaping the media folder); Accept & proceed and Return / Decline / Reject on tasks.
- `accreditation/tests/test_v23_redesign.py`: the case page's seven sections and second row; the folded journey; the portal journey's "Cleared for QA"; the R2 switch (two buckets, never X-Accel, refuses missing settings, private files have no public URL); the directors' workload card and duty conflicts.
- `inspection/tests/test_reports.py::test_team_lead_compilation_is_prefilled_from_accepted_reports`.
- `qa_core/tests/test_phase8_ui.py::test_cqac_shared_vote_setting_is_gone` and `accreditation/tests/test_phase8_decisions.py::test_cqac_members_always_vote_individually`.

## Older tests updated for the owner's decisions

- `accreditation/tests/test_linear_onboarding.py`: Decision 1: clearing the last self-study ends SOP-01 with "Cleared for QA". There is no Director-approve + ES-confirm step any more.
- `accreditation/tests/test_sop03.py (rollback)`: Decision 3: after a rollback the old report and decisions are frozen as versions and the working copy keeps its content. The desk officer applies the rollback only after ES approval.
- `accreditation/tests/test_sop03.py (walk-throughs)`: Decision 4: decision forms start empty, so the walk-through chooses an approval type itself. It also completes the capacity assessment that stage 13 requires.
- `accreditation/tests/test_phase8_decisions.py`: Decision 10: no shared vote. An officer outside the case now gets 404 (cannot see it) instead of 403.
- `accreditation/tests/test_onboarding_ssq.py`: Decision 1: submitted self-studies are not enough; they must be cleared.
- `accounts/test_groups_permissions.py`: User Management is now part of "People & access" in the sidebar.
- `backups/tests/test_backups.py`: Media is backed up as a ZIP, and every backup adds `data.xlsx`.

## Pre-existing failures fixed in this release

These failed on the original upload. Each now passes.

- `accounts/test_departments.py::test_seeded_roles_get_cross_department_right_only_where_intended`
- `accounts/test_fix_m5.py::test_fresh_install_gives_es_role_permissions`
- `accounts/test_groups_permissions.py::test_sidebar_section_visibility`
- `accounts/test_phase9_passkeys.py::test_institution_passkey_signs_in_on_portal`
- `accreditation/tests/test_onboarding_ssq.py::test_sop01_self_study_submission_is_immutable_and_requires_ce`
- `accreditation/tests/test_phase8_decisions.py::test_capacity_is_assessed_by_desk_officer_and_director_confirmed_before_cqac`
- `accreditation/tests/test_phase8_decisions.py::test_decisions_then_stage_17_updates_programmes_and_issues_letters`
- `accreditation/tests/test_sop03.py::test_director_rollback_requires_es_and_reopens_downstream_work`
- `accreditation/tests/test_sop03.py::test_seventeen_stage_walk_through_the_screens`
- `accreditation/tests/test_sop03.py::test_seventeen_stage_walk_with_approvals`
- `accreditation/tests/test_ssq.py::test_reviewer_needs_permission_and_scope`
- `accreditation/tests/test_v21_phase0.py::test_o8_non_returned_documents_stay_locked`
- `accreditation/tests/test_v21_phase0.py::test_o8_returned_document_upload_allowed_under_review`
- `accreditation/tests/test_v21_phase0.py::test_q1_submit_programmes_moves_flow_and_locks`
- `accreditation/tests/test_v21_phase0.py::test_q2_ntird_clear_and_return_per_programme`
- `accreditation/tests/test_v21_phase0.py::test_x12_ict_head_cannot_submit`
- `accreditation/tests/test_x35_x36_shortfall.py::test_x36_shortfall_recorded_at_submission_and_review_card`
- `accreditation/tests/test_x37b_body_requirements.py::test_mandatory_not_met_needs_a_remark_to_accept`
- `accreditation/tests/test_x37b_body_requirements.py::test_met_or_not_applicable_accepts_without_a_remark`
- `accreditation/tests/test_x37b_body_requirements.py::test_requires_body_letter_keeps_the_programme_waiting`
- `api/tests/test_announcements.py::test_attachment_download`
- `api/tests/test_announcements.py::test_create_multipart_with_attachment_and_publish`
- `api/tests/test_chats.py::test_message_shape`
- `api/tests/test_chats.py::test_messages_paging_and_marks_read`
- `api/tests/test_chats.py::test_send_message_and_attachment_download`
- `api/tests/test_chats.py::test_voice_note_flow`
- `api/tests/test_memos.py::test_attachments_and_chat_link`
- `api/tests/test_memos.py::test_counts_match_the_website`
- `api/tests/test_memos.py::test_create_approve_publish_read_acknowledge_archive`
- `api/tests/test_memos.py::test_drafts_publish_and_cancel`
- `api/tests/test_memos.py::test_list_validation_and_search`
- `api/tests/test_memos.py::test_publish_without_route_delivers_directly`
- `api/tests/test_memos.py::test_return_resubmit_and_reject`
- `api/tests/test_qa_staff.py::test_case_act_rules`
- `api/tests/test_qa_staff.py::test_case_attach_and_download`
- `api/tests/test_qa_staff.py::test_case_lists_and_detail`
- `api/tests/test_qa_staff.py::test_complaints_queue_reply_and_act`
- `assignments/tests/test_pp_access.py::test_p11_privacy_mode_is_dual_control`
- `assignments/tests/test_pp_access.py::test_p1_department_mode_is_todays_rule`
- `assignments/tests/test_pp_matrix.py::test_view_all_holders`
- `assignments/tests/test_privacy_mode_migration.py::test_seeded_settings_fit_config_columns`
- `backups/tests/test_backups.py::test_sqlite_run_writes_a_manifest_whose_hashes_verify`
- `config/test_a9_appearance.py::test_v7_components_use_semantic_brand_tokens`
- `config/test_a9_appearance.py::test_v7_pages_load_at_most_five_stylesheets`
- `core/test_p0_env_settings.py::test_s27_env_example_has_no_secrets`
- `core/test_p0_env_settings.py::test_s27_env_example_lists_every_variable`
- `core/test_p0_repo_hygiene.py::test_u5_repo_hygiene_gitignore_patterns`
- `inspection/tests/test_reports.py::test_stage_13_needs_a_recommendation_per_programme`
- `institutions/tests/test_x46_x48_add_tvet.py::test_x48_inline_edit_and_drop`
- `qa_core/tests/test_formula.py::test_seeded_fee_formula_against_real_settings`
- `qa_core/tests/test_seed_startup.py::test_first_run_creates_everything`

## Still failing (already failing before v2.3)

None of these touch the QA screens changed in this release. They are listed so nothing is hidden. The likely cause is given for each group.

### accreditation (5)

Template and wording expectations in the cost page, send-back-to-Audit status, the templates full-flow fixture, and the CE registration page.

- `accreditation/tests/test_sop03.py::test_generate_cost_page_puts_preparation_before_submission`
- `accreditation/tests/test_sop03.py::test_send_back_to_audit_service`
- `accreditation/tests/test_ssq.py::test_return_then_revise_supersedes_never_overwrites`
- `accreditation/tests/test_templates_full_flow.py::test_visit_reports_compute_the_recommendation_and_feed_the_endorsement_chain`
- `accreditation/tests/test_v21_phase0.py::test_x12_registrant_can_register_as_chief_executive`

### api (14)

Payments tests expect Paystack and Remita to be on without gateway settings; the schema route count; the finance and clearance queues; the resource-person home layout.

- `api/tests/test_me_home.py::test_home_resource_person_empty`
- `api/tests/test_payment_return.py::test_bad_token_is_404`
- `api/tests/test_payment_return.py::test_failed_and_pending`
- `api/tests/test_payment_return.py::test_simulated_payment_returns_to_the_app[paystack]`
- `api/tests/test_payment_return.py::test_simulated_payment_returns_to_the_app[remita]`
- `api/tests/test_payment_return.py::test_website_flow_unchanged`
- `api/tests/test_portal_institution.py::test_application_programme_selection`
- `api/tests/test_portal_payments.py::test_online_payment_and_verify`
- `api/tests/test_qa_staff.py::test_clearance_queue_and_decision`
- `api/tests/test_qa_staff.py::test_finance_queue_two_step`
- `api/tests/test_qa_staff.py::test_institutions_scope`
- `api/tests/test_qa_staff.py::test_schema_lists_the_qa_routes`
- `api/tests/test_v21_phase0_api.py::test_q1_programmes_submit_api_ce_only`
- `api/tests/test_v21_phase_e_memos.py::test_m3_api_director_external_waits_for_es`

### assignments (2)

Older `.unscoped` reads in views (the lint), and one department-mode access check.

- `assignments/tests/test_pp_access.py::test_p1_document_assignment_opens_temporary_access_in_department_mode`
- `assignments/tests/test_pp_matrix.py::test_no_unscoped_in_views`

### complaints (3)

The seeded complaint flow count, and portal pages that send the fixture institution to `/setup/`.

- `complaints/tests/test_seed_phase8.py::test_fresh_database_full_seed_is_complete_and_idempotent`
- `complaints/tests/test_views.py::test_portal_knowledge_base_and_nav`
- `complaints/tests/test_views.py::test_portal_raise_list_detail_and_reply`

### core (1)

`seed_nbte` on an empty database runs the startup set a second time.

- `core/test_x51_seed.py::test_seed_empty_db_then_noop`

### inspection (1)

The deputy's return-without-remark message is not on the re-rendered panel.

- `inspection/tests/test_reports.py::test_report_screens_submit_endorse_and_return`

### institutions (91)

Mostly the `approved` fixture: it asks a DPAP director to approve an institution that has no handling department yet, so the action is refused as "not in your scope" (57 tests in `test_actions.py`). The others are old test data: a unique constraint on institution users, Registrar e-mail validation, and professional-body screens that now return 403 to the test user.

- `institutions/tests/test_actions.py::test_add_tvet_creates_proposed_institution_with_inactive_admin`
- `institutions/tests/test_actions.py::test_add_tvet_duplicate_email_rejected_at_initiate`
- `institutions/tests/test_actions.py::test_approve_moves_department`
- `institutions/tests/test_actions.py::test_delisted_institution_cannot_sign_in`
- `institutions/tests/test_actions.py::test_expiry[institutions.cancel_institution_request]`
- `institutions/tests/test_actions.py::test_expiry[institutions.delist]`
- `institutions/tests/test_actions.py::test_expiry[institutions.drop_programmes]`
- `institutions/tests/test_actions.py::test_expiry[institutions.suspend_portal_access]`
- `institutions/tests/test_actions.py::test_expiry[institutions.update_address]`
- `institutions/tests/test_actions.py::test_expiry[institutions.update_category]`
- `institutions/tests/test_actions.py::test_expiry[institutions.update_contact]`
- `institutions/tests/test_actions.py::test_expiry[institutions.update_name]`
- `institutions/tests/test_actions.py::test_expiry[institutions.update_ownership]`
- `institutions/tests/test_actions.py::test_expiry[institutions.update_state_lga]`
- `institutions/tests/test_actions.py::test_expiry[institutions.wipe_requests]`
- `institutions/tests/test_actions.py::test_initiate_pending_confirm_applied[institutions.cancel_institution_request]`
- `institutions/tests/test_actions.py::test_initiate_pending_confirm_applied[institutions.delist]`
- `institutions/tests/test_actions.py::test_initiate_pending_confirm_applied[institutions.drop_programmes]`
- `institutions/tests/test_actions.py::test_initiate_pending_confirm_applied[institutions.suspend_portal_access]`
- `institutions/tests/test_actions.py::test_initiate_pending_confirm_applied[institutions.update_address]`
- `institutions/tests/test_actions.py::test_initiate_pending_confirm_applied[institutions.update_category]`
- `institutions/tests/test_actions.py::test_initiate_pending_confirm_applied[institutions.update_contact]`
- `institutions/tests/test_actions.py::test_initiate_pending_confirm_applied[institutions.update_name]`
- `institutions/tests/test_actions.py::test_initiate_pending_confirm_applied[institutions.update_ownership]`
- `institutions/tests/test_actions.py::test_initiate_pending_confirm_applied[institutions.update_state_lga]`
- `institutions/tests/test_actions.py::test_initiate_pending_confirm_applied[institutions.wipe_requests]`
- `institutions/tests/test_actions.py::test_later_phase_actions_are_disabled`
- `institutions/tests/test_actions.py::test_officer_outside_scope_cannot_initiate`
- `institutions/tests/test_actions.py::test_phase5_actions_are_live_but_refuse_an_invalid_request`
- `institutions/tests/test_actions.py::test_reject_changes_nothing[institutions.cancel_institution_request]`
- `institutions/tests/test_actions.py::test_reject_changes_nothing[institutions.delist]`
- `institutions/tests/test_actions.py::test_reject_changes_nothing[institutions.drop_programmes]`
- `institutions/tests/test_actions.py::test_reject_changes_nothing[institutions.suspend_portal_access]`
- `institutions/tests/test_actions.py::test_reject_changes_nothing[institutions.update_address]`
- `institutions/tests/test_actions.py::test_reject_changes_nothing[institutions.update_category]`
- `institutions/tests/test_actions.py::test_reject_changes_nothing[institutions.update_contact]`
- `institutions/tests/test_actions.py::test_reject_changes_nothing[institutions.update_name]`
- `institutions/tests/test_actions.py::test_reject_changes_nothing[institutions.update_ownership]`
- `institutions/tests/test_actions.py::test_reject_changes_nothing[institutions.update_state_lga]`
- `institutions/tests/test_actions.py::test_reject_changes_nothing[institutions.wipe_requests]`
- `institutions/tests/test_actions.py::test_relist_after_delist`
- `institutions/tests/test_actions.py::test_restore_portal_access`
- `institutions/tests/test_actions.py::test_send_credentials_is_single_step_and_never_sends_a_password`
- `institutions/tests/test_actions.py::test_stale_fails_safely[institutions.cancel_institution_request]`
- `institutions/tests/test_actions.py::test_stale_fails_safely[institutions.delist]`
- `institutions/tests/test_actions.py::test_stale_fails_safely[institutions.drop_programmes]`
- `institutions/tests/test_actions.py::test_stale_fails_safely[institutions.suspend_portal_access]`
- `institutions/tests/test_actions.py::test_stale_fails_safely[institutions.update_address]`
- `institutions/tests/test_actions.py::test_stale_fails_safely[institutions.update_category]`
- `institutions/tests/test_actions.py::test_stale_fails_safely[institutions.update_contact]`
- `institutions/tests/test_actions.py::test_stale_fails_safely[institutions.update_name]`
- `institutions/tests/test_actions.py::test_stale_fails_safely[institutions.update_ownership]`
- `institutions/tests/test_actions.py::test_stale_fails_safely[institutions.update_state_lga]`
- `institutions/tests/test_actions.py::test_stale_fails_safely[institutions.wipe_requests]`
- `institutions/tests/test_actions.py::test_update_category_of_proposed_institution_stays_in_ntird`
- `institutions/tests/test_actions.py::test_update_category_transfers_department`
- `institutions/tests/test_actions.py::test_update_ownership_re_evaluates_documents_and_holds_application`
- `institutions/tests/test_change_requests.py::test_inactive_reviewer_and_lead_cannot_act_on_change_requests`
- `institutions/tests/test_change_requests.py::test_locked_team_changes_require_approved_request`
- `institutions/tests/test_import.py::test_failure_during_apply_rolls_back_every_row`
- `institutions/tests/test_import.py::test_import_screen_flow`
- `institutions/tests/test_import.py::test_round_trip_export_reimport_changes_nothing`
- `institutions/tests/test_import.py::test_stale_batch_is_released_for_resending`
- `institutions/tests/test_import.py::test_valid_sheet_waits_for_second_officer_then_applies`
- `institutions/tests/test_migration.py::test_later_bulk_send_from_the_batch_page`
- `institutions/tests/test_pb_people_reports.py::test_add_member_creates_account_and_membership`
- `institutions/tests/test_pb_people_reports.py::test_add_member_rejects_duplicate_email`
- `institutions/tests/test_pb_people_reports.py::test_assign_representative_creates_visit_external_member`
- `institutions/tests/test_pb_people_reports.py::test_assign_representative_rejects_nonmember`
- `institutions/tests/test_pb_people_reports.py::test_case_reports_tab_shows_body_report`
- `institutions/tests/test_pb_people_reports.py::test_deactivate_and_reactivate`
- `institutions/tests/test_pb_people_reports.py::test_portal_pages_render`
- `institutions/tests/test_pb_people_reports.py::test_portal_person_flow`
- `institutions/tests/test_pb_people_reports.py::test_portal_request_flow`
- `institutions/tests/test_pb_people_reports.py::test_primary_contact_moves`
- `institutions/tests/test_pb_people_reports.py::test_report_chain_rep_to_nbte`
- `institutions/tests/test_pb_people_reports.py::test_report_download_rights`
- `institutions/tests/test_pb_people_reports.py::test_report_pages_scoped`
- `institutions/tests/test_professional_bodies.py::test_add_body_with_inline_errors`
- `institutions/tests/test_professional_bodies.py::test_curriculum_officer_role_can_manage`
- `institutions/tests/test_professional_bodies.py::test_export_downloads_a_workbook`
- `institutions/tests/test_professional_bodies.py::test_import_screen_checks_then_imports`
- `institutions/tests/test_professional_bodies.py::test_list_has_search_filters_and_pagination`
- `institutions/tests/test_professional_bodies.py::test_map_a_programme_and_toggle_it`
- `institutions/tests/test_professional_bodies.py::test_viewers_cannot_edit`
- `institutions/tests/test_programmes_and_records.py::test_body_page_lists_its_programmes_across_institutions`
- `institutions/tests/test_x44_programme_master.py::test_x44_portal_typeahead_and_add`
- `institutions/tests/test_x44_programme_master.py::test_x44_programme_landing_handles_master_rows_without_a_body`
- `institutions/tests/test_x44_programme_master.py::test_x44_unmapped_queue_and_map`
- `institutions/tests/test_x45_master_data_imports.py::test_curriculum_requirements_import`
- `institutions/tests/test_x45_master_data_imports.py::test_management_roles_import`

### leave (1)

Validator messages.

- `leave/test_phase_d.py::test_f5_validators`

### notifications (1)

`check_email` wording.

- `notifications/test_check_email.py::test_reports_missing_configuration`

### payments (1)

Payment slip QR test data.

- `payments/tests/test_payments.py::test_slip_qr_verifies`

### qa_core (21)

The scoping audits flag older `.unscoped` reads in institution views and `payments.LedgerJournal` without `INSTITUTION_PATH`. Also: portal lists that send the fixture institution to `/setup/` (incomplete profile), `seed_startup` granting three role permissions again on every run, and media-organiser path expectations.

- `qa_core/tests/test_host_routing.py::test_every_internal_url_is_404_on_the_portal_host`
- `qa_core/tests/test_host_routing.py::test_url_tables_are_disjoint`
- `qa_core/tests/test_interaction_contract.py::test_portal_pages_full_and_partial`
- `qa_core/tests/test_interaction_contract.py::test_rules_filters_live_in_the_url`
- `qa_core/tests/test_media_storage.py::test_organize_dry_run_then_apply_then_rerun`
- `qa_core/tests/test_media_storage.py::test_organize_never_overwrites`
- `qa_core/tests/test_p0_htmx_history.py::test_n2_fragment_urls_redirect_plain_get_to_full_page[qa_portal:programmes-qa_portal:application]`
- `qa_core/tests/test_pending_actions.py::test_other_department_cannot_confirm_unless_any_department`
- `qa_core/tests/test_phase6_interaction.py::test_case_detail_names_the_stage_out_of_seventeen`
- `qa_core/tests/test_phase6_interaction.py::test_portal_lists_meet_the_contract[/documents/-facets4-params4]`
- `qa_core/tests/test_phase6_interaction.py::test_portal_lists_meet_the_contract[/qa-requests/-facets0-params0]`
- `qa_core/tests/test_phase6_interaction.py::test_portal_request_list_pages_at_25_with_show_all`
- `qa_core/tests/test_portal_public.py::test_generate_institution_guides_builds_four_word_files`
- `qa_core/tests/test_scoping_enforcement.py::test_layer1_no_qa_model_with_an_institution_fk_forgets_institution_path`
- `qa_core/tests/test_scoping_enforcement.py::test_layer2_source_audit_of_the_qa_apps_is_clean`
- `qa_core/tests/test_scoping_enforcement.py::test_layer3_cross_tenant_requests_answer_404`
- `qa_core/tests/test_scoping_enforcement.py::test_scope_to_institution_rules`
- `qa_core/tests/test_security_phase8.py::test_staff_on_the_portal_cannot_open_complaints_or_help`
- `qa_core/tests/test_seed_startup.py::test_dry_run_rolls_back`
- `qa_core/tests/test_seed_startup.py::test_reset_demo_removes_exactly_the_tagged_rows`
- `qa_core/tests/test_seed_startup.py::test_second_run_changes_nothing`

### staff (3)

Role and grade changes by a director outside the staff member's department.

- `staff/test_b8_b11_roles_and_ids.py::test_c2_access_tab_htmx_save`
- `staff/test_b8_b11_roles_and_ids.py::test_c4_create_with_role`
- `staff/test_b8_b11_roles_and_ids.py::test_c5_import_role_and_grade_with_errors`
