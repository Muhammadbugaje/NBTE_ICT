"""Phase 5 interactive walkthrough (Playwright) on a database seeded with
``seed_phase5_demo``: resource persons, the Availability Finder, the visit
record, the post-visit report and its endorsement chain (Team Lead submits →
Division Head endorses → Deputy Director returns with a remark → Team Lead
fixes and resubmits → … Director endorses), the service history and the
resource-person portal. Screenshots go to SHOTS/phase5.

Usage (server running on the seeded copy, demo passwords set):
    python scripts/walkthroughs/phase5_walk.py
"""
import lib
from lib import *  # noqa: F401,F403

lib.SHOTS = os.path.join(S, "phase5")
os.makedirs(lib.SHOTS, exist_ok=True)
D = "@demo.nbte.local"
_base_shot = shot


def shot(p, name, full=True):  # noqa: F811 — full-page shots: keep the sticky sidebar at the top
    if full:
        p.add_style_tag(content="@media (min-width:1024px){.app-sidebar{position:relative!important;height:auto!important;align-self:stretch!important}}")
    _base_shot(p, name, full)

EEE = "Electrical/Electronic Engineering Technology"


def open_report(p, programme=EEE):
    p.goto(INT + "/qa/inspection/inspections/"); settle(p)
    p.locator("a.qa-rowlink").first.click(); settle(p)
    link = p.locator(f"a.qa-rowlink:has-text('{programme}')").first
    href = link.get_attribute("href")
    link.click(); settle(p)
    return INT + href


def press(p, label):
    p.locator(f"button:has-text('{label}'):visible").first.click(); settle(p); p.wait_for_timeout(500)


# 1. The QA Directorate's view: resource persons, nominations, finder, calendar
c = ctx(); p = page_for(c)
login_internal(p, "qa.director" + D)
p.goto(INT + "/qa/inspection/resource-persons/"); settle(p)
shot(p, "01_resource_persons_list")
p.fill("input[name=q]", "Garba"); p.wait_for_timeout(900); settle(p)
shot(p, "02_resource_persons_search_no_longer_eligible")
p.goto(INT + "/qa/inspection/resource-persons/?eligibility=lapsed"); settle(p)
p.goto(INT + "/qa/inspection/nominations/"); settle(p)
shot(p, "03_nominations_waiting_for_a_vetter")
p.locator("a.qa-rowlink, table a").first.click(); settle(p)
shot(p, "04_resource_person_detail_vetting")
p.goto(INT + "/qa/inspection/personnel/"); settle(p)
shot(p, "05_qa_personnel")
c.close()
c = ctx(); p = page_for(c)  # the desk officer proposes mappings (Map), a Director confirms
login_internal(p, "qa.officer" + D)
p.goto(INT + "/qa/inspection/finder/"); settle(p)
case_choice = p.evaluate("() => { const o = [...document.querySelectorAll('select[name=case] option')].find(o => o.value); return o ? o.value : null; }")
if case_choice:
    p.select_option("select[name=case]", case_choice); settle(p); p.wait_for_timeout(800)
shot(p, "06_availability_finder")
show_all = p.locator("select[name=show]")
if show_all.count():
    show_all.select_option("all"); settle(p); p.wait_for_timeout(800)
    shot(p, "07_availability_finder_with_reasons")
p.goto(INT + "/qa/inspection/mappings/"); settle(p)
shot(p, "08_qa_mappings")
p.goto(INT + "/qa/inspection/calendar/"); settle(p)
shot(p, "09_visit_calendar")
c.close()

# 2. Team Lead: the recorded visit, then writes and submits the report (verification step 3)
c = ctx(); p = page_for(c)
login_internal(p, "qa.team_lead" + D)
p.goto(INT + "/qa/inspection/inspections/"); settle(p)
shot(p, "10_inspections_list")
p.locator("a.qa-rowlink").first.click(); settle(p)
shot(p, "11_inspection_detail_attendance")
p.locator("summary:has-text('Override post-visit personnel')").click(); p.wait_for_timeout(300)
shot(p, "12_override_post_visit_personnel_form")
report_url = open_report(p)
shot(p, "13_report_draft_b17_with_rp_findings")
press(p, "Submit to the Division Head")
shot(p, "14_report_submit_inline_errors")
p.fill("textarea[name=f_facilities]", "Classrooms and laboratories match the SSQ; two benches short in the "
                                       "electrical workshop.")
p.fill("textarea[name=f_staffing]", "Staff list verified; one HOD position is acting.")
p.fill("textarea[name=f_curriculum]", "NBTE curriculum in use; ND II logbooks inspected.")
p.fill("textarea[name=summary]", "The programme meets the minimum standard with two minor deficiencies "
                                 "noted for follow-up.")
p.select_option("select[name=recommendation]", "accredit")
press(p, "Submit to the Division Head")
shot(p, "15_report_submitted_chain")
c.close()

# 3. Division Head endorses (step 4)
c = ctx(); p = page_for(c)
login_internal(p, "qa.division_head" + D)
p.goto(INT + "/qa/inspection/endorsements/?mine=1"); settle(p)
shot(p, "16_endorsement_queue_division_head")
p.goto(report_url); settle(p)
shot(p, "17_division_head_endorse_panel")
p.fill("textarea[name=content]", "Facilities findings agree with the SSQ.")
press(p, "Endorse")
shot(p, "18_after_division_head_endorsed")
c.close()

# 4. Deputy Director returns — first without a remark (refused inline), then with one (step 5)
c = ctx(); p = page_for(c)
login_internal(p, "qa.deputy_director" + D)
p.goto(report_url); settle(p)
press(p, "Return with remark")
shot(p, "19_return_needs_a_remark")
p.fill("textarea[name=content]", "Quantify the deficiencies (benches, acting HOD) before I verify.")
press(p, "Return with remark")
shot(p, "20_returned_to_the_team_lead")
c.close()

# 5. Team Lead fixes and resubmits (step 6)
c = ctx(); p = page_for(c)
login_internal(p, "qa.team_lead" + D)
p.goto(INT + "/tasks/assigned/"); settle(p)
shot(p, "21_team_lead_task_inbox_returned")
p.goto(report_url); settle(p)
shot(p, "22_pinned_remark_on_returned_report")
p.fill("textarea[name=summary]", "The programme meets the minimum standard. Deficiencies: two workshop benches "
                                 "short; the HOD post is filled in an acting capacity.")
press(p, "Re-submit")
shot(p, "23_resubmitted_version_2")
c.close()

# 6. The chain completes: Division Head, Deputy Director, Director endorse
for who in ("qa.division_head", "qa.deputy_director", "qa.director"):
    c = ctx(); p = page_for(c)
    login_internal(p, who + D)
    p.goto(report_url); settle(p)
    press(p, "Endorse")
    if who == "qa.director":
        shot(p, "24_director_endorsed_history")
    c.close()

# 7. Service history (step 7)
c = ctx(); p = page_for(c)
login_internal(p, "qa.director" + D)
p.goto(INT + "/qa/inspection/service-history/"); settle(p)
shot(p, "25_service_history")
p.fill("input[name=q]", "Yakubu"); p.wait_for_timeout(900); settle(p)
shot(p, "26_service_history_filtered_person")
p.goto(INT + "/qa/inspection/endorsements/?status=all"); settle(p)
shot(p, "27_endorsements_all_reports")
cases = p.evaluate("() => [...document.querySelectorAll('a[href*=\"/qa/cases/\"]')].map(a => a.href)")
p.goto(INT + "/qa/cases/"); settle(p)
case_href = p.locator("a.qa-card-case").first.get_attribute("href")
p.goto(INT + case_href.split("?")[0] + "?tab=reports"); settle(p)
shot(p, "28_case_reports_tab")
c.close()

# 8. The resource person's portal and the institution's inspections list
c = ctx(); p = page_for(c)
login_portal(p, "rp.yakubu" + D)
p.goto(POR + "/engagements/?status=all"); settle(p)
shot(p, "29_portal_rp_engagements")
p.goto(POR + "/service-history/"); settle(p)
shot(p, "30_portal_rp_service_history")
p.goto(POR + "/profile/"); settle(p)
shot(p, "31_portal_rp_profile_cv")
c.close()
c = ctx(); p = page_for(c)
login_portal(p, "admin@p4demo.local")
p.goto(POR + "/inspections/"); settle(p)
shot(p, "32_portal_institution_inspections")
c.close()

# 9. Phone width
c = ctx(mobile=True); p = page_for(c)
login_internal(p, "qa.team_lead" + D)
p.goto(report_url); settle(p)
shot(p, "33_mobile_report")
p.goto(INT + "/qa/inspection/calendar/"); settle(p)
shot(p, "34_mobile_calendar_list")
c.close()

print("errors:", json.dumps(errors, indent=1))
browser.close(); pw.stop()
