# Security review — ExternalUserGuardMiddleware (Phase 8, first pass)

Scope: `accounts/middleware.py::ExternalUserGuardMiddleware` and the routes added in Phase 7.
Tests: `qa_core/tests/test_security_phase8.py` (new) plus the existing `test_external_guard.py`.

| # | Check | Result |
|---|---|---|
| 1 | Institution / resource-person sessions on every internal `/qa/…` route | 403 and signed out — asserted for **every** route, including `/qa/complaints/…` |
| 2 | Staff on the portal | Only the Staff Portal Dashboard and own account pages; complaints, help, payments, timeline send them back (HTMX: `HX-Redirect`) |
| 3 | Deployment under a script prefix | **Fixed.** The staff check resolved `request.path`, which includes `SCRIPT_NAME`; it never resolved, and the "unknown path → let the portal 404" branch let staff through. It now resolves `request.path_info`. |
| 4 | Anonymous requests | Not touched by the guard; each view requires sign-in |
| 5 | Impersonation | Unchanged: an impersonating superuser is never logged out; only the stop URL is allowed internally |
| 6 | Complaint visibility | Not institution scoping alone (Finance sees every institution for billing): raiser, same-institution accounts, routed department, referred/escalated people, oversight (ES, ICT monitor) |
| 7 | Internal notes | Filtered in the portal view and in raiser notifications (officer name only when `qa.complaints.show_officer_name_to_raiser`) |
| 8 | Object routes | 404 (never 403) across institutions and departments, portal and internal |

Residual notes (no change made):
- `PASS_PREFIXES` checks `request.path`; under a script prefix static files would simply go through the normal checks (stricter, not weaker).
- `set_demo_passwords` refuses to run when `DEBUG` is off.
