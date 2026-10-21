"""Build PHASE5_WALKTHROUGH.docx (python-docx) from the Phase 5 screenshots
and diagrams.

    python scripts/build_phase5_doc.py [--shots PHASE5_UI_SCREENSHOTS] [--out PHASE5_WALKTHROUGH.docx]
                                       [--credentials PHASE5_DEMO_CREDENTIALS.md]

The credentials table is read from PHASE5_DEMO_CREDENTIALS.md (the table the
seed printed); the diagrams from docs/phase5/*.png (Graphviz sources next to them).
"""
import argparse
import datetime
import os
import re

from docx import Document
from docx.enum.section import WD_ORIENT  # noqa: F401
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GREEN = RGBColor(0x1F, 0x6B, 0x3E)
INK = RGBColor(0x28, 0x2D, 0x33)
MUTED = RGBColor(0x5A, 0x61, 0x6B)
VERSION = "1.0"
MASTER_PLAN = "MASTER_PLAN.md"  # NBTE-QA-MasterPlan-v1.1-20261016

# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------
STEPS = [
    ("Step 1: Log in as the Director and open the resource-person register",
     "Sign in to the internal system as qa.director@demo.nbte.local and choose Inspection (§9) → Resource persons "
     "in the sidebar. The register lists every expert NBTE can call on, with search and four filters (status, "
     "source, discipline, eligibility), 25 per page or Show all. Mr. Garba Lawal carries the red No longer "
     "eligible badge: his rank is no longer in qa.resource_person.required_designations, so his confirmed work "
     "continues but nobody can map him to anything new.",
     ["01_resource_persons_list", "02_resource_persons_search_no_longer_eligible"]),
    ("Step 2: Vet a nomination",
     "Open Nominations. Mrs. Halima Sule was nominated by the desk officer and waits for a vetter. Open her record: "
     "the vetting panel shows her CV and the rules checked. The nominator can never vet their own nomination, a CV "
     "is required, and rejecting needs a remark. Approving makes her active.",
     ["03_nominations_waiting_for_a_vetter", "04_resource_person_detail_vetting"]),
    ("Step 3: Check the QA personnel",
     "QA personnel are NBTE staff registered for QA duty through the two-officer action Edit QA Personnel. Rank "
     "(qa.resource_person.min_rank) and designation are checked when the registration is started and again when "
     "it is confirmed. There is no override.",
     ["05_qa_personnel"]),
    ("Step 4: Log in as the desk officer and use the Availability Finder",
     "Sign in as qa.officer@demo.nbte.local (officers propose mappings; a Director confirms them) and open Availability finder. Choose the demo case. The finder lists only people who pass every §9 rule for "
     "that programme, duty and dates, ranked by fewest visits this year. Switch Show to 'Also show who is not "
     "eligible, and why' to see each refusal with the rule and the setting behind it: conflict of interest (Mr. "
     "Emeka Nwosu works at the institution), annual cap (Prof. Bashir Musa), discipline (Dr. Ifeoma Eze), status "
     "(nominated), rank. Map books the person under a lock; if another officer booked the same person a moment "
     "earlier, the second save is refused.",
     ["06_availability_finder", "07_availability_finder_with_reasons"]),
    ("Step 5: Review the mappings and the Visit Calendar",
     "QA mappings shows who is mapped to which programme, each confirmed by a second officer (the pending-action "
     "reference is shown). The Visit Calendar shows the month's visits; on a phone it becomes a list.",
     ["08_qa_mappings", "09_visit_calendar"]),
    ("Step 6: Log in as the Team Lead and look at the recorded visit",
     "Sign in as qa.team_lead@demo.nbte.local. The ES's visit approval created the inspection; the demo has "
     "already recorded the visit (Mark the visit completed) on the two days before today — earlier than its "
     "planned dates, so that you can try stage 12 straight away; in real use the Team Lead records it after the "
     "visit. The post-visit personnel list shows who attended. "
     "Attendance is the ground truth for allowances and the service history. If it is wrong, the Team Lead uses "
     "Override post-visit personnel: a second officer confirms and the service history receives compensating rows.",
     ["10_inspections_list", "11_inspection_detail_attendance", "12_override_post_visit_personnel_form"]),
    ("Step 7: Write and submit the post-visit report (verification step 3)",
     "Open the Electrical/Electronic Engineering Technology report. Only people who attended can write it (B17). "
     "The engineering resource person's findings, submitted on the portal, are shown for the team. Pressing Submit "
     "with an empty summary shows the errors next to the fields. Fill in the sections, the summary and the "
     "recommendation, then Submit to the Division Head: the Team Lead's task closes and the Division Head gets one.",
     ["13_report_draft_b17_with_rp_findings", "14_report_submit_inline_errors", "15_report_submitted_chain"]),
    ("Step 8: Log in as the Division Head and endorse (verification step 4)",
     "Sign in as qa.division_head@demo.nbte.local. Endorsements → Waiting on me lists the report. Open it, add a "
     "comment if you like, and press Endorse. The report moves to the Deputy Director.",
     ["16_endorsement_queue_division_head", "17_division_head_endorse_panel", "18_after_division_head_endorsed"]),
    ("Step 9: Log in as the Deputy Director and return with a remark (verification step 5)",
     "Sign in as qa.deputy_director@demo.nbte.local and open the report. Press Return with remark without writing "
     "anything: the system refuses and says a return needs a remark. Write the remark and return it: the report goes "
     "back to the person who submitted it, with the remark pinned on their task.",
     ["19_return_needs_a_remark", "20_returned_to_the_team_lead"]),
    ("Step 10: Log in as the Team Lead, fix and resubmit (verification step 6)",
     "The Team Lead's Tasks inbox has the returned report, marked high priority. Open it: the Deputy Director's "
     "remark is pinned above the form. Correct the summary and press Re-submit. The version goes to 2 and the chain "
     "starts again at the Division Head.",
     ["21_team_lead_task_inbox_returned", "22_pinned_remark_on_returned_report", "23_resubmitted_version_2"]),
    ("Step 11: The chain completes",
     "The Division Head, the Deputy Director and the Director each endorse. The endorsement history keeps every "
     "step, including the return and its remark. When the Director endorses the last programme's report, the case "
     "moves to stage 13 on its own.",
     ["24_director_endorsed_history"]),
    ("Step 12: Check the service record (verification step 7)",
     "Open Service history. Every attendee of the visit has a row, written when the visit was recorded; filter by "
     "person (Yakubu), institution, programme, discipline, duty or dates, and export to Excel. Rows are never edited "
     "or deleted: a corrected attendance adds a compensating row. The case's Reports tab shows each report's place "
     "in the chain and, at stage 13, the Director's recommendation form per programme.",
     ["25_service_history", "26_service_history_filtered_person", "27_endorsements_all_reports",
      "28_case_reports_tab"]),
    ("Step 13: The resource person's portal and the institution's view",
     "On the QA portal, rp.yakubu@demo.nbte.local sees My engagements (with the findings form), the service history "
     "and Profile & CV. The institution's admin (admin@p4demo.local) sees a read-only list of its inspections.",
     ["29_portal_rp_engagements", "30_portal_rp_service_history", "31_portal_rp_profile_cv",
      "32_portal_institution_inspections"]),
    ("Step 14: On a phone",
     "Every Phase 5 screen works at phone width: the report form stacks, and the calendar becomes a list.",
     ["33_mobile_report", "34_mobile_calendar_list"]),
]

VERIFICATION = [
    ("1", "Migrate", "python manage.py migrate (after backing up db.sqlite3)", "Two migrations applied"),
    ("2", "Seed", "python manage.py seed_phase5_demo", "Case at stage 12, visit recorded, two reports in draft"),
    ("3", "Team Lead submits the report", "Steps 6–7", "Report submitted; Division Head has the task"),
    ("4", "Division Head endorses", "Step 8", "Report with the Deputy Director"),
    ("5", "Deputy Director returns with a remark", "Step 9", "Empty remark refused; with a remark → returned to the Team Lead, remark pinned"),
    ("6", "Team Lead fixes and resubmits", "Step 10", "Version 2, back with the Division Head"),
    ("7", "Check the service record", "Step 12", "A row per attendee; compensating rows for corrections"),
]

APPENDIX_PLAN = [
    ("§5.5 inspection (data model)", "#55-inspection",
     "ResourcePerson, QAPersonnel, QAMapping, Inspection, InspectionTeamMember, PostVisitPersonnel, PostVisitReport, "
     "ReportEndorsement, ServiceRecord, SSQReviewAssignment"),
    ("§7 Two-step confirmation pattern", "#7-two-step-confirmation-pattern",
     "Edit QA Personnel, Edit QA Mapping, Override Post-Visit Personnel"),
    ("§8 Task bounce", "#8-task-bounce", "Report tasks per endorsement layer; returns to the submitter; reconciliation"),
    ("§9 Resource-person and SSQ tracking", "#9-resource-person-and-ssq-tracking",
     "Service history, the Available-for-the-next-visit picker, double-booking enforcement"),
    ("§9 Eligibility rules (v1.1)", "#eligibility-rules-v11", "The seven rules, no manual override, falling below later"),
    ("§14 Dynamic settings catalogue", "#14-dynamic-settings-catalogue", "qa.resource_person.*, qa.matching.*"),
    ("§20 Interaction contract", "#20-interaction-contract", "Search, filters, 25-row pages, Show all, HTMX, skeletons"),
    ("PHASES.md — Phase 5", "PHASES.md", "Scope of this phase"),
]

SETTINGS = [
    ("qa.resource_person.min_rank", "director", "Rule 1 — minimum role rank for NBTE staff"),
    ("qa.resource_person.required_designations", "director, deputy-director, chief-lecturer, principal-lecturer",
     "Rule 2 — allowed designations / rank codes"),
    ("qa.resource_person.max_active_engagements", "2", "Rule 3 — live mappings (all one programme, R-11)"),
    ("qa.resource_person.max_open_ssq_per_officer", "3", "Rule 4 — open SSQ reviews (R-01)"),
    ("qa.resource_person.max_visits_per_year", "6", "Rule 5 — annual visit cap"),
    ("qa.institution.admin_required_department", "any", "Rule 6 — Institutional Admin's department"),
    ("qa.institution.chief_executive_required_position", "true", "Rule 7 — Chief Executive is the head"),
    ("qa.matching.travel_buffer_days", "2", "Days kept free before and after a visit"),
    ("qa.matching.same_institution_cooldown_months", "24", "Cooldown before serving the same institution again"),
    ("qa.matching.allow_secondary_discipline", "false", "Secondary disciplines count in the finder"),
    ("qa.matching.rank_score", "formula", "Finder ranking (fewest visits, longest since served, rating)"),
]

TESTS = [
    ("inspection/tests/test_eligibility.py", "The seven §9 rules at nomination and assignment; no override; vetting; "
                                             "nightly list of people no longer eligible"),
    ("inspection/tests/test_availability.py", "Every finder filter, ranking, the finder screen, two officers with the "
                                              "same view, two threads booking at once, re-check at confirmation"),
    ("inspection/tests/test_reports.py", "Inspection from the ES approval, visit record, B17, submit validation, the "
                                         "endorsement chain with return + remark, stage 12 → 13, stage 13 "
                                         "recommendation and return, compensating service rows, screens, ICT "
                                         "monitor read-only, the resource-person portal"),
    ("accreditation/tests/test_sop03.py", "Phase 4's 17-stage walks, updated for the new stage 12"),
]


# ---------------------------------------------------------------------------
# docx helpers
# ---------------------------------------------------------------------------
def set_cell_shading(cell, hex_fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr.append(shd)


def add_hyperlink(paragraph, url, text):
    part = paragraph.part
    r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                          is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    r_pr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "1F6B3E")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    r_pr.append(color)
    r_pr.append(underline)
    run.append(r_pr)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def table(doc, header, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(header):
        cell = t.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(h)
        run.bold = True
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        set_cell_shading(cell, "1F6B3E")
    for row in rows:
        cells = t.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = str(value)
    if widths:
        t.autofit = False
        for i, col in enumerate(t.columns):
            col.width = widths[i]
    for row in t.rows:
        for i, cell in enumerate(row.cells):
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9)
            if widths:
                cell.width = widths[i]
    doc.add_paragraph()
    return t


def figure(doc, path, caption, width_cm=16.0):
    if not os.path.exists(path):
        p = doc.add_paragraph(f"[Screenshot not found: {os.path.basename(path)}]")
        p.runs[0].italic = True
        return False
    from PIL import Image

    with Image.open(path) as im:
        w, h = im.size
    max_h = 21.5  # cm: keep a tall full-page screenshot on one page with its caption
    if width_cm * h / w > max_h:
        doc.add_picture(path, height=Cm(max_h))
    else:
        doc.add_picture(path, width=Cm(width_cm))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap = doc.add_paragraph(caption)
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.runs[0].italic = True
    cap.runs[0].font.size = Pt(9)
    cap.runs[0].font.color.rgb = MUTED
    return True


def read_credentials(path):
    """Rows of the first Markdown table under '## Accounts' in the credentials file."""
    if not os.path.exists(path):
        return []
    rows, in_table = [], False
    for line in open(path, encoding="utf-8"):
        if line.startswith("| Who") or line.startswith("| who"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            if set(line.strip()) <= set("|-: "):
                continue
            cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
            rows.append(cells)
    return rows


def style_document(doc):
    styles = doc.styles
    styles["Normal"].font.name = "Calibri"
    styles["Normal"].font.size = Pt(11)
    styles["Normal"].font.color.rgb = INK
    for name, size in (("Heading 1", 18), ("Heading 2", 14), ("Heading 3", 12)):
        styles[name].font.color.rgb = GREEN
        styles[name].font.size = Pt(size)
        styles[name].font.name = "Calibri"
    for section in doc.sections:
        section.left_margin = section.right_margin = Cm(2.2)
        section.top_margin = section.bottom_margin = Cm(2.0)
        footer = section.footer.paragraphs[0]
        footer.text = f"NBTE Digital QA System · Phase 5 walkthrough · v{VERSION}"
        footer.runs[0].font.size = Pt(8)
        footer.runs[0].font.color.rgb = MUTED


# ---------------------------------------------------------------------------
def build(shots, out, credentials):
    doc = Document()
    style_document(doc)

    # Cover
    for _ in range(5):
        doc.add_paragraph()
    p = doc.add_paragraph()
    run = p.add_run("National Board for Technical Education")
    run.font.size = Pt(13)
    run.font.color.rgb = MUTED
    p = doc.add_paragraph()
    run = p.add_run("NBTE Digital Quality Assurance System")
    run.bold = True
    run.font.size = Pt(26)
    run.font.color.rgb = GREEN
    p = doc.add_paragraph()
    run = p.add_run("Phase 5 walkthrough — Inspection and resource persons")
    run.font.size = Pt(18)
    doc.add_paragraph()
    meta = doc.add_paragraph()
    meta.add_run(f"Version {VERSION}\n").bold = True
    meta.add_run(datetime.date.today().strftime("%-d %B %Y") + "\n")
    meta.add_run("Master Plan v1.1 §5.5, §9 · SOP-03 stages 12–13")
    doc.add_paragraph()
    intro = doc.add_paragraph(
        "Phase 5 adds the people side of quality assurance. NBTE keeps a vetted register of resource persons and QA "
        "personnel. The Availability Finder shows who is eligible and free for a visit, with every §9 rule enforced "
        "and no override. A second officer confirms each mapping, so nobody is booked twice. When the Executive "
        "Secretary approves a visit, the inspection is created. After the visit, the Team Lead records who actually "
        "went, which writes the service history. Stage 12 is a real post-visit report for each programme: only "
        "attendees can write it (B17), and it is endorsed by the Division Head, the Deputy Director and the Director, "
        "who can each endorse, add content or return it with a remark. Stage 13 records the Directorate's "
        "recommendation for each programme.")
    intro.paragraph_format.space_after = Pt(6)
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # 1. Before you start
    doc.add_heading("1. Before you start", level=1)
    doc.add_paragraph("Run these once, from the project folder. Back up the database first.")
    for cmd in ("cp db.sqlite3 db.sqlite3.before-phase5-$(date +%Y%m%d-%H%M%S)",
                "python manage.py migrate",
                "python manage.py seed_phase5_demo",
                "python manage.py runserver 0.0.0.0:8000"):
        para = doc.add_paragraph(style="List Bullet")
        r = para.add_run(cmd)
        r.font.name = "Consolas"
        r.font.size = Pt(10)
    doc.add_paragraph(
        "seed_phase5_demo is safe to run again. It prints every demo account with its password, the case, the "
        "inspection and who holds each report. Run it with --reset-passwords for new passwords. The internal system "
        "is at http://localhost:8000; the QA portal at its own host (QA_PORTAL_HOST, for example http://qa.localhost:8000).")

    # 2. Credentials
    doc.add_heading("2. Demo accounts", level=1)
    creds = read_credentials(credentials)
    if creds:
        table(doc, ["Who", "E-mail (sign-in)", "Password", "Role · department", "In this walkthrough"], creds,
              widths=[Cm(2.3), Cm(5.4), Cm(2.6), Cm(2.9), Cm(3.4)])
    doc.add_paragraph(
        "The passwords are the ones seed_phase5_demo printed on this database. If you reset them, use the new ones "
        "it prints. Every new password must be changed at first sign-in when DEBUG is off.")

    # 3. Diagrams
    doc.add_heading("3. How it fits together", level=1)
    doc.add_heading("The endorsement chain (B14)", level=2)
    doc.add_paragraph(
        "Each programme's report goes through three endorsement layers. Every layer can endorse it (it moves on), "
        "add content (it stays), or return it with a remark (it goes back to whoever submitted it). A resubmitted "
        "report starts again at the Division Head.")
    figure(doc, os.path.join(ROOT, "docs", "phase5", "endorsement_chain.png"),
           "Figure 1 — Division Head → Deputy Director → Director, with returns to the submitter", 16.5)
    doc.add_heading("Stages 8 to 13", level=2)
    figure(doc, os.path.join(ROOT, "docs", "phase5", "stages_10_13.png"),
           "Figure 2 — From visit details to the Directorate's recommendation", 13.5)

    # 4. Walkthrough
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    doc.add_heading("4. Step-by-step walkthrough", level=1)
    n = 0
    for title, text, names in STEPS:
        doc.add_heading(title, level=2)
        doc.add_paragraph(text)
        for name in names:
            n += 1
            label = name.split("_", 1)[1].replace("_", " ")
            figure(doc, os.path.join(shots, name + ".png"), f"Screenshot {n} — {label}",
                   7.5 if name.startswith("3") and "mobile" in name else 16.0)

    # 5. Verification
    doc.add_heading("5. Interactive verification checklist", level=1)
    table(doc, ["#", "Check", "How", "Expected"], VERIFICATION, widths=[Cm(0.8), Cm(4.5), Cm(5.0), Cm(7.0)])

    # Appendices
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    doc.add_heading("Appendix A — Master Plan references", level=1)
    doc.add_paragraph("Links open the Master Plan v1.1 (MASTER_PLAN.md in NBTE-QA-MasterPlan-v1.1-20261016) at the "
                      "section this phase implements.")
    for title, anchor, what in APPENDIX_PLAN:
        para = doc.add_paragraph(style="List Bullet")
        target = anchor if anchor.endswith(".md") else MASTER_PLAN + anchor
        add_hyperlink(para, target, title)
        para.add_run(f" — {what}")
    doc.add_heading("Appendix B — Settings used by Phase 5", level=1)
    table(doc, ["Setting", "Default", "What it controls"], SETTINGS, widths=[Cm(6.5), Cm(4.5), Cm(6.5)])
    doc.add_paragraph("Change them in Settings → QA settings. Changing a setting is audited and is the only way to "
                      "change who qualifies; there is no override.")
    doc.add_heading("Appendix C — Automated tests", level=1)
    table(doc, ["File", "What it proves"], TESTS, widths=[Cm(6.0), Cm(11.5)])
    doc.add_heading("Appendix D — Delivery documents", level=1)
    for name in ("PHASE5_CHANGELOG.md", "PHASE5_MIGRATIONS.md", "PHASE5_TEST_PLAN.md", "PHASE5_DEMO_CREDENTIALS.md",
                 "PHASE5_DONE.md", "PHASE5_UI_SCREENSHOTS/"):
        doc.add_paragraph(name, style="List Bullet")
    doc.save(out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=os.path.join(ROOT, "PHASE5_UI_SCREENSHOTS"))
    ap.add_argument("--out", default=os.path.join(ROOT, "PHASE5_WALKTHROUGH.docx"))
    ap.add_argument("--credentials", default=os.path.join(ROOT, "PHASE5_DEMO_CREDENTIALS.md"))
    a = ap.parse_args()
    print(build(a.shots, a.out, a.credentials))
