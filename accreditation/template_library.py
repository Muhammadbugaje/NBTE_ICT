"""v2.4 — complete, published SSQ and visit-report templates for every QA type.

Authoring format: the SSQ engine's JSON schema (``accreditation/ssq.py``),
written here with the same ``_f`` helper as ``qa_core/seed_data_phase3.py``.
Every template stays editable afterwards in Settings → SSQ templates (a
published version is never edited; a change becomes the next version).

Sources the content follows
---------------------------
* NBTE *Programme Evaluation Form for Institutions Offering Programmes for
  which Accreditation/Reaccreditation is Sought* (Form NBTE/P/4A, 2022):
  sections I–VII (academic matters, physical facilities, staffing,
  entrepreneurship, funding, summary, recommendations), the four-point
  Very good / Good / Fair / Poor scale and the guide notes on observations
  that lead to interim (deferred) or denied accreditation.
* NBTE *Standards for the Accreditation of Diploma Programmes in Polytechnics
  and Similar Post-Secondary Technical Institutions in Nigeria*: types of
  accreditation, curriculum structure (foundation 10–15 %, core 60–70 %,
  general studies 10–15 %, SIWES of at least 4 months), staffing (minimum 4
  lecturers per single-stream programme, 2 more per extra HND option,
  pro-rated 1:15 science/technology and 1:20 others; HOD Senior Lecturer for
  ND, Principal Lecturer for HND; teaching load 8–18 h), facility use
  factors, library volumes (stream size × 45 titles × 3 volumes; about 8 % of
  recurrent cost), training consumables (₦5,000 tech / ₦3,500 business per
  student per year), employers' rating / tracer studies.
* NBTE/QA/ICT/VVT/001 v2 (the owner's ICT Verification Visit Report) — the
  ICT section of every programme SSQ declares exactly what that visit tests
  (workstations, bandwidth, LMS, e-library, power, backup, cybersecurity).
* The NBTE minimum facilities and equipment per ND/HND curriculum (the
  facilities register): pulled into each SSQ dynamically through the two
  ``catalogue`` fields (see ``accreditation/register_link.py``).

NBTE does not publish numeric weights for the panel's ratings; the weights and
pass marks in ``PROGRAMME_EVALUATION_REPORT["scoring"]`` are documented
defaults (editable as a new template version).
"""
from __future__ import annotations

import copy

SEED_TAG = "nbte-v2.4-full"


def _f(key, ftype, label, **extra):
    field = {"key": key, "type": ftype, "label": label, "required": extra.pop("required", False)}
    field.update(extra)
    return field


def _opts(*pairs):
    return [{"value": v, "label": label} for v, label in pairs]


def _col(key, label, ctype="text", **extra):
    col = {"key": key, "label": label, "type": ctype}
    col.update(extra)
    return col


YES_NO = "yes_no"
HALF = {"width": "half"}

RANKS = ["Chief Lecturer", "Principal Lecturer", "Senior Lecturer", "Lecturer I", "Lecturer II", "Lecturer III",
         "Assistant Lecturer", "Chief Instructor", "Principal Instructor", "Senior Instructor", "Instructor"]
EMPLOYMENT = ["Full-time", "Part-time", "Contract", "Visiting"]
LEVELS = ["ND I", "ND II", "HND I", "HND II", "NID I", "NID II"]


# ===========================================================================
# Programme SSQ sections (shared building blocks)
# ===========================================================================
def sec_programme(variant="accreditation"):
    fields = [
        _f("programme_name", "text", "Programme", required=True, prefill_from="programme.name"),
        _f("programme_level", "text", "Award level (ND / HND / NID)", required=True, prefill_from="programme.level",
           **HALF),
        _f("programme_category", "text", "Programme category", prefill_from="programme.category", **HALF),
        _f("school_name", "text", "School / faculty", required=True, **HALF),
        _f("department_name", "text", "Department offering the programme", required=True, **HALF),
        _f("hnd_options", "textarea", "Options offered (HND), one per line",
           help="Leave blank for ND or an HND without options. Each additional HND option needs two more lecturers."),
        _f("programme_goal", "textarea", "Goal and objectives of the programme", required=True,
           help="The type of manpower the programme produces and what its diplomates can do on graduation "
                "(NBTE/P/4A §1.1). Note any additions for local needs.", validation={"min_length": 40}),
        _f("commencement_date", "date", "Date the programme commenced (first intake)",
           prefill_from="programme.commencement_date", required=variant != "resource_inspection", **HALF),
        _f("approved_streams", "integer", "Approved streams", prefill_from="programme.approved_streams",
           validation={"min": 0, "max": 20}, **HALF),
        _f("running_streams", "integer",
           "Streams you plan to run" if variant == "resource_inspection" else "Streams currently running",
           required=True, validation={"min": 0, "max": 20}, **HALF),
        _f("stream_size", "integer", "Students per stream", required=True, validation={"min": 1, "max": 200},
           help="NBTE norm: 30 for science and technology, 40 for business and social-science programmes.", **HALF),
        _f("programme_type", "choice", "Programme type (for staffing and funding norms)", required=True,
           options=_opts(("technology", "Science / technology based"), ("business", "Business / social science"),
                         ("arts", "Arts / humanities"), ("health", "Health / allied")), **HALF),
    ]
    if variant in ("accreditation", "resource_inspection", "reaccreditation", "validation"):
        label = {"resource_inspection": "Letter approving the establishment of the programme (or the request)",
                 "accreditation": "NBTE approval to commence / interim accreditation letter",
                 "reaccreditation": "Last NBTE accreditation letter",
                 "validation": "Last NBTE accreditation letter"}[variant]
        fields.append(_f("status_letter", "file", label, required=variant != "resource_inspection"))
    return {"key": "programme", "title": "Programme particulars", "department": True,
            "description": "Basic facts about the programme and the department that runs it.", "fields": fields}


def sec_headship():
    return {"key": "headship", "title": "Head of department", "department": True,
            "description": "NBTE: an ND department is headed by at least a Senior Lecturer with 10 years' "
                           "post-qualification experience; HND by at least a Principal Lecturer with 13 years. "
                           "The HOD holds a higher degree in the discipline (not an allied one) and is registered "
                           "with the professional body.",
            "fields": [
                _f("hod_name", "text", "Name of the head of department", required=True, **HALF),
                _f("hod_rank", "choice", "Rank", required=True, options=RANKS[:7], **HALF),
                _f("hod_qualifications", "textarea", "Qualifications (with institutions and years)", required=True),
                _f("hod_years_experience", "integer", "Years of post-qualification experience", required=True,
                   validation={"min": 0, "max": 60}, **HALF),
                _f("hod_registered", YES_NO, "Registered with the relevant professional body?", required=True,
                   **HALF),
                _f("hod_body", "text", "Professional body and membership number", **HALF),
                _f("hod_teaching_hours", "integer", "HOD's weekly teaching load (hours)",
                   validation={"min": 0, "max": 40}, help="NBTE maximum for an HOD: 8 hours a week.", **HALF),
                _f("hod_appointment", "file", "HOD's letter of appointment / CV"),
            ]}


def sec_curriculum():
    return {"key": "curriculum", "title": "Curriculum and academic regulations", "department": True,
            "description": "NBTE/P/4A §1.2 and §1.4. Structure expected: foundation courses 10–15 %, "
                           "professional/core 60–70 %, general studies 10–15 %, a project, and supervised "
                           "industrial work experience (SIWES) of at least four months.",
            "fields": [
                _f("nbte_curriculum", YES_NO, "Is the current NBTE curriculum for the programme in use?",
                   required=True, **HALF),
                _f("curriculum_year", "integer", "Year of the curriculum edition in use", required=True,
                   validation={"min": 1980, "max": 2100}, **HALF),
                _f("foundation_pct", "number", "Foundation courses (% of contact hours)", required=True,
                   validation={"min": 0, "max": 100}, **HALF),
                _f("core_pct", "number", "Professional / core courses (% of contact hours)", required=True,
                   validation={"min": 0, "max": 100}, **HALF),
                _f("gns_pct", "number", "General studies (% of contact hours)", required=True,
                   validation={"min": 0, "max": 100}, **HALF),
                _f("siwes_months", "integer", "Length of SIWES (months)", required=True,
                   validation={"min": 0, "max": 12}, **HALF),
                _f("course_units", YES_NO, "Are courses in units, with contents in behavioural objectives and "
                                          "level-coded?", required=True, **HALF),
                _f("entrepreneurship_courses", YES_NO, "Are the entrepreneurship courses taught?", required=True,
                   **HALF),
                _f("local_content", "textarea", "Courses or content added for industry or local needs"),
                _f("deviations", "textarea", "Deviations from the NBTE curriculum, and why"),
                _f("academic_regulations", YES_NO, "Are academic regulations published in a students' handbook?",
                   required=True, help="Entry requirements, examinations, grading, malpractice penalties, "
                                       "withdrawal, probation, graduation.", **HALF),
                _f("regulations_updated", "integer", "Year the handbook was last revised",
                   validation={"min": 1980, "max": 2100}, **HALF),
                _f("curriculum_copy", "file", "Curriculum in use (copy)", required=True),
                _f("students_handbook", "file", "Students' handbook / academic regulations", required=True),
                _f("timetable", "file", "Current lecture and practical timetable", required=True,
                   help="The ICT verification visit uses it to count laboratory sessions per week."),
            ]}


def sec_students(variant="accreditation"):
    fields = [
        _f("entry_compliance_pct", "number",
           "Students enrolled who meet NBTE minimum entry requirements (%)", required=True,
           validation={"min": 0, "max": 100},
           help="ND: five credits (SSCE/NECO/GCE/NABTEB) in relevant subjects. HND: at least a lower credit in "
                "the relevant ND plus one year's post-ND cognate work experience.", **HALF),
        _f("admission_deficient", "integer", "Students who do not meet the entry requirements",
           required=True, validation={"min": 0}, **HALF),
        _f("enrolment", "table", "Enrolment by level and session (last three sessions)", required=True, min_rows=1,
           columns=[_col("session", "Session"), _col("level", "Level", "choice", options=LEVELS),
                    _col("male", "Male", "integer"), _col("female", "Female", "integer"),
                    _col("total", "Total", "integer")]),
        _f("admission_list", "file", "Admission list with entry qualifications (current session)", required=True),
        _f("deficient_students", "textarea", "Students not meeting entry requirements and what is being done"),
    ]
    if variant in ("accreditation", "reaccreditation", "validation"):
        fields += [
            _f("graduates", "table", "Graduates (initial accreditation: first set; reaccreditation: last two sets)",
               required=variant != "accreditation",
               min_rows=1 if variant != "accreditation" else 0,
               columns=[_col("session", "Session"), _col("graduated", "Graduated", "integer"),
                        _col("distinction", "Distinction", "integer"), _col("upper", "Upper credit", "integer"),
                        _col("lower", "Lower credit", "integer"), _col("pass", "Pass", "integer")]),
        ]
    return {"key": "students", "title": "Admission and enrolment", "department": True,
            "description": "NBTE/P/4A §1.3. NBTE confirms that every enrolled student meets the entry requirements; "
                           "more than 20 % admission-deficient students defers accreditation.",
            "fields": fields}


def sec_teaching():
    return {"key": "teaching", "title": "Teaching, examinations and students' work", "department": True,
            "description": "NBTE/P/4A §1.4–1.10 for the past four semesters. Have question papers, marking schemes, "
                           "answer scripts, practical log books, projects and external examiners' reports ready for "
                           "the panel.",
            "fields": [
                _f("exam_format", YES_NO, "Do semester examinations follow the NBTE format?", required=True,
                   help="3-unit course: answer 5 of at least 7 questions in 3 hours; 2-unit: 4 of 6 in 2 hours.",
                   **HALF),
                _f("marking_schemes", "choice", "Marking schemes for the past four semesters", required=True,
                   options=_opts(("all", "Available for every course and examination"),
                                 ("most", "Available for most courses"), ("few", "Available for a few courses"),
                                 ("none", "Not available")), **HALF),
                _f("success_rate_pct", "number", "Success rate over the past four semesters (%)", required=True,
                   validation={"min": 0, "max": 100}, help="Below 70 % defers accreditation (NBTE/P/4A §1.6).",
                   **HALF),
                _f("practical_coverage_pct", "number", "Practical content covered and recorded (%)", required=True,
                   validation={"min": 0, "max": 100}, help="Below 70 % defers accreditation (NBTE/P/4A §1.7).",
                   **HALF),
                _f("practical_manuals", YES_NO, "Practical manuals for every course with practicals?",
                   required=True, **HALF),
                _f("log_books", YES_NO, "Standard practical note / log books in use?", required=True, **HALF),
                _f("projects_supervised", YES_NO, "Are final-year projects supervised and graded with comments?",
                   required=True, **HALF),
                _f("practicals_graded_pct", "number", "Practicals and projects satisfactorily graded (%)",
                   validation={"min": 0, "max": 100}, **HALF),
                _f("external_examiners", "table", "External examiners / moderators (last two sessions)",
                   columns=[_col("name", "Name"), _col("qualification", "Qualification"), _col("rank", "Rank"),
                            _col("organisation", "Organisation"),
                            _col("source", "From", "choice", options=["Academia", "Industry"]),
                            _col("appointed", "Year appointed", "integer")]),
                _f("moderation_implemented", YES_NO, "Were the external examiners' recommendations implemented?",
                   **HALF),
                _f("results", "file", "Semester results for the past four semesters", required=True),
                _f("external_reports", "file", "External examiners' reports"),
            ]}


def sec_academic_staff(variant="accreditation"):
    proposed = variant == "resource_inspection"
    return {"key": "academic_staff", "title": "Academic staff", "department": True,
            "description": "NBTE minimum for a single-stream programme: 4 qualified lecturers (or 3 and an "
                           "instructor), 2 more for each additional HND option; more streams pro-rated at one "
                           "lecturer to 15 students (science/technology) or 20 (others), excluding general studies. "
                           "Mix: at least 1 Principal/Chief Lecturer, 2 Senior Lecturers, 3–5 Lecturer I and below. "
                           "Weekly load 8–18 hours.",
            "fields": [
                _f("core_staff", "table", "Core lecturers and instructors" + (" (recruited or to be recruited)"
                                                                             if proposed else ""),
                   required=True, min_rows=3,
                   columns=[_col("name", "Name"), _col("rank", "Rank", "choice", options=RANKS),
                            _col("qualification", "Highest qualification"), _col("specialisation", "Specialisation"),
                            _col("experience", "Years' experience", "integer"),
                            _col("registration", "Professional registration"),
                            _col("employment", "Employment", "choice", options=EMPLOYMENT),
                            _col("load", "Hours a week", "integer"), _col("courses", "Courses taught")]),
                _f("service_staff", "table", "Service lecturers (from other departments)",
                   columns=[_col("name", "Name"), _col("department", "Home department"),
                            _col("rank", "Rank", "choice", options=RANKS), _col("qualification", "Qualification"),
                            _col("courses", "Courses taught"), _col("load", "Hours a week", "integer")]),
                _f("max_teaching_load", "integer", "Highest weekly teaching load of any core lecturer (hours)",
                   required=True, validation={"min": 0, "max": 60}, **HALF),
                _f("staff_on_leave", "integer", "Core staff on study / sabbatical / leave of absence",
                   validation={"min": 0}, **HALF),
                _f("nominal_roll", "file", "Signed nominal roll of the department's staff", required=True,
                   help="With letters of appointment and certificates available for the panel."),
            ]}


def sec_support_staff():
    return {"key": "support_staff", "title": "Technical and administrative staff", "department": True,
            "description": "Major laboratories and workshops are supervised by technologists (HND or equivalent); "
                           "smaller ones by technicians (ND) under a technologist, with attendants. The department "
                           "office has a secretary (ND/HND Office Technology and Management) and a clerical officer.",
            "fields": [
                _f("technical_staff", "table", "Technologists, technicians, instructors and attendants",
                   required=True, min_rows=1,
                   columns=[_col("name", "Name"),
                            _col("cadre", "Cadre", "choice", options=["Technologist", "Technician", "Instructor",
                                                                      "Attendant", "Farm manager"]),
                            _col("qualification", "Qualification"), _col("facility", "Facility manned"),
                            _col("experience", "Years' experience", "integer")]),
                _f("secretary", YES_NO, "Does the department have a secretary?", required=True, **HALF),
                _f("secretary_qualification", "text", "Secretary's qualification", **HALF),
                _f("clerical_staff", "integer", "Clerical officers / typists", validation={"min": 0}, **HALF),
                _f("cleaners", "integer", "Cleaners / messengers", validation={"min": 0}, **HALF),
            ]}


def sec_staff_development():
    return {"key": "staff_development", "title": "Staff development", "department": True,
            "description": "Teaching staff should attend relevant in-service training at least once every five "
                           "years under a well-executed staff development policy (NBTE/P/4A §3.6).",
            "fields": [
                _f("development_policy", YES_NO, "Is there a written staff development policy?", required=True,
                   **HALF),
                _f("staff_trained_5y", "integer", "Teaching staff trained in the last five years", required=True,
                   validation={"min": 0}, **HALF),
                _f("training", "table", "Training in the last five years",
                   columns=[_col("name", "Staff"), _col("training", "Course / conference / placement"),
                            _col("provider", "Provider"), _col("year", "Year", "integer"),
                            _col("kind", "Kind", "choice", options=["Long-term (degree)", "Short course",
                                                                    "Industrial placement", "Conference/workshop"])]),
                _f("development_policy_file", "file", "Staff development policy"),
            ]}


def sec_facilities():
    return {"key": "facilities", "title": "Physical facilities", "department": True,
            "description": "Classrooms: 30 seats (science/technology) or 40 (business), use factor ≤ 0.8 (40 h/week). "
                           "Laboratories, workshops and studios: about 30 students, use factor ≤ 0.6–0.7. Required "
                           "facilities come from NBTE's minimum list for the curriculum: enter how many you have.",
            "fields": [
                _f("classrooms", "integer", "Classrooms / lecture theatres available to the programme",
                   required=True, validation={"min": 0}, **HALF),
                _f("classroom_seats", "integer", "Total classroom seats", required=True, validation={"min": 0},
                   **HALF),
                _f("classrooms_shared", YES_NO, "Are classrooms pooled with other departments?", required=True,
                   **HALF),
                _f("classroom_hours", "integer", "Classroom hours used per week (busiest room)",
                   validation={"min": 0, "max": 80}, **HALF),
                _f("teaching_spaces", "table", "Laboratories, workshops, studios and drawing rooms used",
                   required=True, min_rows=1,
                   columns=[_col("name", "Name"),
                            _col("kind", "Kind", "choice", options=["Laboratory", "Workshop", "Studio",
                                                                    "Drawing room", "Farm / field", "Other"]),
                            _col("capacity", "Seats / work places", "integer"), _col("area", "Area (m²)", "number"),
                            _col("hours", "Hours used per week", "integer"),
                            _col("condition", "Condition", "choice", options=["Good", "Fair", "Poor"])]),
                _f("facilities", "catalogue", "Facilities NBTE's curriculum requires (laboratories, workshops, studios)",
                   catalogue={"kind": "facility"},
                   help="Enter 1 for each required facility you have (more if you run several)."),
                _f("staff_offices", "integer", "Staff offices", required=True, validation={"min": 0}, **HALF),
                _f("staff_sharing", "integer", "Staff sharing an office with more than one other person",
                   validation={"min": 0}, **HALF),
                _f("safety", "multi_choice", "Safety provisions in laboratories and workshops", required=True,
                   options=["Fire extinguishers (in date)", "First-aid boxes", "Safety signage and rules",
                            "Protective equipment (PPE)", "Emergency exits", "Fume cupboards / extraction"]),
                _f("utilities", "multi_choice", "Utilities supplied to teaching spaces", required=True,
                   options=["Public power", "Standby generator", "Solar / inverter", "Running water", "Gas"]),
                _f("facility_photos", "file", "Photographs of facilities (with location and date)", multiple=True),
            ]}


def sec_equipment():
    return {"key": "equipment", "title": "Equipment against NBTE's minimum list", "department": True,
            "description": "The list is NBTE's minimum equipment for this curriculum and level, from the facilities "
                           "register. Enter only items that work today; the panel counts them during the visit and "
                           "a declared item that is not produced is treated as falsified.",
            "fields": [
                _f("equipment", "catalogue", "Equipment, tools and instruments", catalogue={"kind": "equipment"}),
                _f("other_equipment", "table", "Other equipment you hold for the programme (not on the list)",
                   columns=[_col("name", "Item"), _col("quantity", "Quantity", "integer"),
                            _col("working", "Working", "integer"), _col("location", "Location")]),
                _f("equipment_engraved", YES_NO, "Is new equipment engraved, installed and commissioned?",
                   required=True, **HALF),
                _f("maintenance", YES_NO, "Is there a maintenance schedule for major equipment?", required=True,
                   **HALF),
                _f("equipment_register", "file", "Departmental equipment / asset register", required=True),
            ]}


def sec_library():
    return {"key": "library", "title": "Library and learning resources", "department": True,
            "description": "NBTE norm: students per stream × about 45 titles (3 per course for ~15 courses) × 3 "
                           "volumes, current and diversified, with journals, non-book materials and e-resources. "
                           "About 8 % of the institution's recurrent cost should maintain the library.",
            "fields": [
                _f("library_titles", "integer", "Titles relevant to the programme", required=True,
                   validation={"min": 0}, **HALF),
                _f("library_volumes", "integer", "Volumes relevant to the programme", required=True,
                   validation={"min": 0}, **HALF),
                _f("journals", "integer", "Journals / periodicals (print or electronic)", required=True,
                   validation={"min": 0}, **HALF),
                _f("recent_titles_pct", "number", "Titles published in the last ten years (%)",
                   validation={"min": 0, "max": 100}, **HALF),
                _f("e_library", YES_NO, "Is there an e-library?", required=True, **HALF),
                _f("e_resources", "textarea", "E-resource subscriptions (databases, platforms) and their dates"),
                _f("departmental_library", YES_NO, "Is there a departmental library / reading room?", **HALF),
                _f("library_seats", "integer", "Library seats available to students", validation={"min": 0},
                   **HALF),
                _f("library_list", "file", "List of programme titles held (author, title, year, copies)",
                   required=True),
            ]}


def sec_entrepreneurship():
    return {"key": "entrepreneurship", "title": "Entrepreneurship development", "department": False,
            "description": "NBTE/P/4A section IV. A centre whose skill areas each take at least 40 students at a "
                           "time, a business centre, show and display rooms; a curriculum to NBTE specification; "
                           "projects recorded in log books. No entrepreneurship curriculum at all denies "
                           "accreditation.",
            "fields": [
                _f("ent_centre", YES_NO, "Is there an Entrepreneurship Development Centre?", required=True, **HALF),
                _f("ent_curriculum", "choice", "Entrepreneurship curriculum", required=True,
                   options=_opts(("nbte_local", "NBTE curriculum with local content"),
                                 ("nbte", "NBTE curriculum adopted as it is"),
                                 ("below", "Below the minimum standard"), ("none", "No curriculum")), **HALF),
                _f("ent_skills", "table", "Skill areas offered",
                   columns=[_col("skill", "Skill area"), _col("capacity", "Students at a time", "integer"),
                            _col("equipment", "Equipment", "choice", options=["Adequate", "Partly", "Inadequate"])]),
                _f("ent_projects", YES_NO, "Are students' entrepreneurship projects undertaken and recorded?",
                   required=True, **HALF),
                _f("ent_records", "file", "Entrepreneurship project records / log books"),
            ]}


def sec_funding():
    return {"key": "funding", "title": "Funding of the programme", "department": False,
            "description": "NBTE norm for training consumables and imprest: at least ₦5,000 per student a year for "
                           "technology programmes and ₦3,500 for business programmes, released when due.",
            "fields": [
                _f("recurrent_allocation", "number", "Annual allocation for consumables and imprest (₦)",
                   required=True, validation={"min": 0}, **HALF),
                _f("capital_allocation", "number", "Capital allocation to the department last year (₦)",
                   validation={"min": 0}, **HALF),
                _f("released_when_due", YES_NO, "Are imprest and consumables released when due?", required=True,
                   **HALF),
                _f("salaries_regular", YES_NO, "Have staff salaries and allowances been paid regularly in the last "
                                              "12 months?", required=True, **HALF),
                _f("library_budget_pct", "number", "Share of the institution's recurrent budget spent on the library "
                                                   "(%)", validation={"min": 0, "max": 100}, **HALF),
                _f("budget_evidence", "file", "Budget / release evidence for the department", required=True),
            ]}


def sec_industry(variant="accreditation"):
    return {"key": "industry", "title": "Employers' rating, SIWES and industry links", "department": True,
            "description": "NBTE/P/4A §1.12: institutions that have graduated students track their diplomates "
                           "(employers' rating / tracer study); others use supervised SIWES reports.",
            "fields": [
                _f("has_graduates", YES_NO, "Has the programme graduated students?", required=True, **HALF),
                _f("employer_rating", "choice", "Employers' rating of diplomates (latest tracer study)",
                   options=_opts(("very_good", "Very good"), ("good", "Good"), ("fair", "Fair"), ("poor", "Poor"),
                                 ("none", "No tracer study yet")), required=variant == "reaccreditation", **HALF),
                _f("tracer_study", "file", "Tracer study / employers' feedback", required=variant == "reaccreditation"),
                _f("siwes_placed_pct", "number", "Students placed for SIWES in the last session (%)",
                   required=variant != "resource_inspection", validation={"min": 0, "max": 100}, **HALF),
                _f("siwes_supervised", YES_NO, "Is SIWES supervised and graded by the department?",
                   required=variant != "resource_inspection", **HALF),
                _f("industry_links", "table", "Industry partners",
                   columns=[_col("organisation", "Organisation"), _col("nature", "Nature of the link"),
                            _col("since", "Since (year)", "integer")]),
                _f("siwes_reports", "file", "SIWES supervision reports / ITF returns"),
            ]}


def sec_ict():
    return {"key": "ict", "title": "ICT provision (declared for the ICT verification visit)", "department": False,
            "description": "These figures are tested on site by NBTE's ICT officer (form NBTE/QA/ICT/VVT/001): "
                           "count only machines that work, give bandwidth you can demonstrate.",
            "fields": [
                _f("ict_workstations", "integer", "Workstations available to this programme's students",
                   required=True, validation={"min": 0}, **HALF),
                _f("ict_workstations_shared", YES_NO, "Are these workstations shared with other programmes?",
                   required=True, **HALF),
                _f("ict_bandwidth_mbps", "number", "Internet bandwidth (Mbps, dedicated)", required=True,
                   validation={"min": 0}, **HALF),
                _f("ict_provider", "text", "Internet service provider and plan", **HALF),
                _f("ict_coverage", "multi_choice", "Where students and staff have working connectivity",
                   required=True, options=["Administration", "Teaching laboratories", "Library / e-library",
                                           "Hostels", "Staff offices", "Campus-wide Wi-Fi"]),
                _f("ict_lms", "text", "Learning Management System (name and version)", **HALF),
                _f("ict_lms_url", "url", "LMS address", **HALF),
                _f("ict_elibrary_remote", YES_NO, "Can the e-library be reached from outside the campus?",
                   required=True, **HALF),
                _f("ict_power_backup", "text", "Power backup for ICT (e.g. 20 kVA inverter, 4 hours)", required=True,
                   **HALF),
                _f("ict_staff", "integer", "ICT technical staff (institution ICT unit)", validation={"min": 0},
                   **HALF),
                _f("ict_cybersecurity", "multi_choice", "Cybersecurity in place",
                   options=["Firewall", "Endpoint protection / antivirus", "Patch management", "Access control policy",
                            "Security awareness training"]),
                _f("ict_backup", "choice", "Data backup", required=True,
                   options=_opts(("daily", "Daily, restore tested"), ("weekly", "Weekly"),
                                 ("irregular", "Irregular"), ("none", "None")), **HALF),
                _f("ict_email_domain", "text", "Official e-mail domain in use (e.g. @poly.edu.ng)", **HALF),
            ]}


def sec_previous_visit():
    return {"key": "previous_visit", "title": "Previous accreditation visit", "department": True,
            "description": "Reaccreditation tests that the earlier status is warranted, sustained or improved.",
            "fields": [
                _f("last_visit_date", "date", "Date of the last accreditation visit", required=True, **HALF),
                _f("last_status", "choice", "Status granted", required=True,
                   options=_opts(("full", "Full accreditation"), ("interim", "Interim accreditation"),
                                 ("denied", "Denied")), **HALF),
                _f("last_expiry", "date", "Current accreditation expires", prefill_from="programme.expiry_date",
                   **HALF),
                _f("weaknesses_noted", "textarea", "Weaknesses noted by the last panel", required=True),
                _f("weaknesses_addressed", "textarea", "How each weakness was addressed", required=True),
                _f("corrective_evidence", "file", "Evidence of corrective action"),
            ]}


def sec_declaration():
    return {"key": "declaration", "title": "Declaration", "department": False,
            "description": "Signed for the institution before submission. A false declaration attracts sanctions, "
                           "including a fine per falsified item found at the visit.",
            "fields": [
                _f("declarant_name", "text", "Name of the officer making the declaration", required=True, **HALF),
                _f("declarant_designation", "text", "Designation", required=True, **HALF),
                _f("declaration", YES_NO, "I declare that the information and evidence in this questionnaire are "
                                         "true and complete.", required=True),
            ]}


# ===========================================================================
# Programme SSQs per QA type
# ===========================================================================
def accreditation_ssq():
    return {
        "intro": "Self-study questionnaire for programme ACCREDITATION (initial accreditation of a programme with "
                 "approval to commence or interim accreditation). Based on NBTE Form NBTE/P/4A and the Standards for "
                 "the Accreditation of Diploma Programmes. One questionnaire per programme; sections marked for the "
                 "department may be sent to the academic department. The facilities and equipment lists come from "
                 "NBTE's minimum list for the curriculum.",
        "sections": [sec_programme("accreditation"), sec_headship(), sec_curriculum(), sec_students("accreditation"),
                     sec_teaching(), sec_academic_staff(), sec_support_staff(), sec_staff_development(),
                     sec_facilities(), sec_equipment(), sec_library(), sec_entrepreneurship(), sec_funding(),
                     sec_industry("accreditation"), sec_ict(), sec_declaration()],
    }


def reaccreditation_ssq():
    schema = accreditation_ssq()
    schema["intro"] = ("Self-study questionnaire for programme REACCREDITATION (renewal after the accreditation "
                       "period, normally five years). Covers the last two sets of graduates and how the weaknesses "
                       "noted at the last visit were addressed.")
    secs = [sec_programme("reaccreditation"), sec_headship(), sec_curriculum(), sec_students("reaccreditation"),
            sec_teaching(), sec_academic_staff(), sec_support_staff(), sec_staff_development(), sec_facilities(),
            sec_equipment(), sec_library(), sec_entrepreneurship(), sec_funding(), sec_industry("reaccreditation"),
            sec_ict(), sec_previous_visit(), sec_declaration()]
    schema["sections"] = secs
    return schema


def resource_inspection_ssq():
    teaching_plan = {"key": "readiness", "title": "Readiness to mount the programme", "department": True,
                     "description": "NBTE grants interim accreditation (approval to commence) only after its staff and "
                                    "resource persons confirm that the department has all the resources required to "
                                    "mount and sustain the programme.",
                     "fields": [
                         _f("proposed_intake", "integer", "Proposed first intake (students)", required=True,
                            validation={"min": 1}, **HALF),
                         _f("proposed_start", "date", "Proposed commencement date", required=True, **HALF),
                         _f("justification", "textarea", "Need for the programme (labour market, catchment, "
                                                         "employers consulted)", required=True,
                            validation={"min_length": 40}),
                         _f("feasibility", "file", "Feasibility / needs-assessment report", required=True),
                         _f("council_approval", "file", "Governing Council / Academic Board approval", required=True),
                         _f("siwes_plan", "textarea", "Plan for SIWES placements and industry partners",
                            required=True),
                     ]}
    return {
        "intro": "Self-study questionnaire for a RESOURCE INSPECTION of a proposed programme (before it may admit "
                 "students). Declare the staff recruited, the facilities and equipment in place against NBTE's "
                 "minimum list, library holdings and funding.",
        "sections": [sec_programme("resource_inspection"), teaching_plan, sec_headship(), sec_curriculum(),
                     sec_academic_staff("resource_inspection"), sec_support_staff(), sec_facilities(),
                     sec_equipment(), sec_library(), sec_entrepreneurship(), sec_funding(),
                     sec_industry("resource_inspection"), sec_ict(), sec_declaration()],
    }


def increase_capacity_ssq():
    capacity = {"key": "capacity", "title": "Carrying capacity requested", "department": True,
                "description": "Each additional stream needs proportionate staff (1:15 science/technology, 1:20 "
                               "others), classroom and laboratory/workshop places within the use factors, equipment "
                               "and library volumes.",
                "fields": [
                    _f("current_streams", "integer", "Streams approved now", prefill_from="programme.approved_streams",
                       required=True, validation={"min": 0, "max": 20}, **HALF),
                    _f("requested_streams", "integer", "Streams requested in total", required=True,
                       validation={"min": 1, "max": 20}, **HALF),
                    _f("current_enrolment", "integer", "Current enrolment across all levels", required=True,
                       validation={"min": 0}, **HALF),
                    _f("applicants_last", "integer", "Qualified applicants last admission season", required=True,
                       validation={"min": 0}, **HALF),
                    _f("capacity_justification", "textarea", "Why more streams are needed", required=True,
                       validation={"min_length": 40}),
                    _f("additional_staff", "table", "Staff added for the new streams",
                       columns=[_col("name", "Name"), _col("rank", "Rank", "choice", options=RANKS),
                                _col("qualification", "Qualification"), _col("appointed", "Date appointed", "date")]),
                    _f("additional_spaces", "textarea", "Classrooms, laboratories and workshops added or extended"),
                    _f("capacity_timetable", "file", "Timetable showing how the extra streams will be taught",
                       required=True),
                ]}
    return {
        "intro": "Self-study questionnaire for an INCREASE IN CARRYING CAPACITY (more streams) of an accredited "
                 "programme. NBTE's capacity engine checks staff, classroom, laboratory and library capacity "
                 "against the streams requested.",
        "sections": [sec_programme("increase_capacity"), capacity, sec_academic_staff(), sec_support_staff(),
                     sec_facilities(), sec_equipment(), sec_library(), sec_funding(), sec_ict(), sec_declaration()],
    }


def validation_ssq():
    changes = {"key": "changes", "title": "Changes since accreditation", "department": True,
               "fields": [
                   _f("changes_summary", "textarea", "Significant changes since the last accreditation (staff, "
                                                    "facilities, curriculum, leadership)", required=True),
                   _f("last_visit_date", "date", "Date of the last accreditation visit", required=True, **HALF),
                   _f("weaknesses_addressed", "textarea", "Progress on the weaknesses noted at the last visit",
                      required=True),
               ]}
    return {
        "intro": "Self-study questionnaire for a VALIDATION visit to an accredited programme: confirms the "
                 "accreditation conditions are being sustained.",
        "sections": [sec_programme("validation"), changes, sec_students("validation"), sec_academic_staff(),
                     sec_facilities(), sec_equipment(), sec_library(), sec_ict(), sec_declaration()],
    }


def verification_ssq():
    facts = {"key": "facts", "title": "Facts to be verified", "department": False,
             "description": "List each fact the institution reported (or NBTE asked to verify) and the evidence.",
             "fields": [
                 _f("verification_subject", "choice", "Subject of the verification", required=True,
                    options=_opts(("facilities", "Facilities and equipment"), ("staff", "Staff"),
                                  ("students", "Students / results"), ("site", "Site / premises"),
                                  ("ict", "ICT provision"), ("other", "Other"))),
                 _f("facts", "table", "Facts reported", required=True, min_rows=1,
                    columns=[_col("item", "Item"), _col("declared", "Declared figure / statement"),
                             _col("location", "Where it can be seen"), _col("evidence", "Evidence reference")]),
                 _f("verification_evidence", "file", "Supporting evidence", required=True, multiple=True),
                 _f("contact_person", "text", "Contact person for the visit", required=True, **HALF),
                 _f("contact_phone", "phone", "Contact telephone", required=True, **HALF),
             ]}
    return {
        "intro": "Self-study questionnaire for a VERIFICATION visit: NBTE verifies facts the institution reported.",
        "sections": [sec_programme("verification"), facts, sec_equipment(), sec_ict(), sec_declaration()],
    }


def institutional_administration_ssq():
    """Institution-level SSQ (Standards: philosophy, organisation and control,
    governing council, academic board, admission and student welfare,
    finance, physical facilities, library, ICT)."""
    return {
        "intro": "Self-study questionnaire for INSTITUTIONAL ADMINISTRATION (assessed as a programme in its own "
                 "right on the same cycle). Fields marked 'from your record' come from your accepted NBTE record.",
        "sections": [
            {"key": "particulars", "title": "Institution particulars", "department": False,
             "fields": [
                 _f("institution_name", "text", "Name of institution", required=True, prefill_from="institution.name"),
                 _f("ownership", "text", "Ownership", required=True, prefill_from="institution.ownership", **HALF),
                 _f("institution_type", "text", "Type", prefill_from="institution.type", **HALF),
                 _f("year_established", "integer", "Year established", required=True,
                    prefill_from="institution.year_established", validation={"min": 1900, "max": 2100}, **HALF),
                 _f("state", "text", "State", prefill_from="institution.state", **HALF),
                 _f("address", "textarea", "Permanent-site address", required=True, prefill_from="institution.address"),
                 _f("official_email", "email", "Official e-mail", required=True,
                    prefill_from="institution.contacts.official_email", **HALF),
                 _f("phone", "phone", "Telephone", prefill_from="institution.contacts.phone", **HALF),
                 _f("website", "url", "Website", prefill_from="institution.website"),
                 _f("philosophy", "textarea", "Philosophy, mission and objectives of the institution", required=True),
             ]},
            {"key": "governance", "title": "Governing Council and principal officers", "department": False,
             "fields": [
                 _f("council_constituted", YES_NO, "Is the Governing Council constituted?", required=True, **HALF),
                 _f("council_inaugurated", "date", "Date the current council was inaugurated", **HALF),
                 _f("council_chair", "text", "Chairman of Council", required=True),
                 _f("council_members", "table", "Members of Council", required=True, min_rows=3,
                    columns=[_col("name", "Name"), _col("designation", "Designation"),
                             _col("representing", "Representing")]),
                 _f("council_meetings", "integer", "Council meetings in the last session", required=True,
                    validation={"min": 0, "max": 50}, **HALF),
                 _f("head_of_institution", "text", "Head of institution (Rector / Provost)", required=True,
                    prefill_from="institution.contacts.head_of_institution.name", **HALF),
                 _f("principal_officers", "table", "Principal officers", required=True, min_rows=3,
                    columns=[_col("name", "Name"),
                             _col("post", "Post", "choice", options=["Rector / Provost", "Deputy Rector", "Registrar",
                                                                     "Bursar", "Librarian", "Director of Works",
                                                                     "Other"]),
                             _col("qualification", "Highest qualification"), _col("appointed", "Appointed", "date"),
                             _col("status", "Status", "choice", options=["Substantive", "Acting"])]),
                 _f("organogram", "file", "Organogram", required=True, document_kind="organogram",
                    prefill_from="document.organogram"),
             ]},
            {"key": "academic_board", "title": "Academic Board and academic administration", "department": False,
             "fields": [
                 _f("academic_board", YES_NO, "Is the Academic Board constituted (all Chief Lecturers included)?",
                    required=True, **HALF),
                 _f("board_meetings", "integer", "Academic Board meetings in the last session", required=True,
                    validation={"min": 0}, **HALF),
                 _f("schools", "table", "Schools and departments", required=True, min_rows=1,
                    columns=[_col("school", "School"), _col("department", "Department"),
                             _col("head", "Head"), _col("programmes", "Programmes", "integer")]),
                 _f("academic_calendar", YES_NO, "Approved academic calendar for the current session?",
                    required=True, **HALF),
                 _f("exam_malpractice_committee", YES_NO, "Examination malpractice committee in place?",
                    required=True, **HALF),
                 _f("academic_master_plan", "file", "Academic master plan", document_kind="academic-master-plan",
                    prefill_from="document.academic-master-plan"),
             ]},
            {"key": "statutory", "title": "Legal establishment and land", "department": False,
             "fields": [
                 _f("enabling_law", "file", "Enabling law / edict (public)", document_kind="enabling-law",
                    prefill_from="document.enabling-law"),
                 _f("cac_certificate", "file", "CAC certificate (private)", document_kind="cac",
                    prefill_from="document.cac"),
                 _f("land_title", "file", "Certificate of Occupancy / land title", required=True,
                    document_kind="c-of-o", prefill_from="document.c-of-o"),
                 _f("land_size", "number", "Land area (hectares)", required=True, validation={"min": 0}, **HALF),
                 _f("physical_master_plan", "file", "Physical master plan", document_kind="physical-master-plan",
                    prefill_from="document.physical-master-plan"),
             ]},
            {"key": "students", "title": "Admission, students and welfare", "department": False,
             "fields": [
                 _f("total_students", "integer", "Students enrolled (current session)", required=True,
                    validation={"min": 0}, **HALF),
                 _f("admission_policy", YES_NO, "Published policy on admission, probation, withdrawal and "
                                               "graduation?", required=True, **HALF),
                 _f("student_services", "multi_choice", "Student welfare services", required=True,
                    options=["Hostel accommodation", "Health centre / clinic", "Guidance and counselling",
                             "Sports facilities", "Cafeteria", "Students' union", "Transport", "Security"]),
                 _f("hostel_spaces", "integer", "Hostel bed spaces", validation={"min": 0}, **HALF),
                 _f("health_staff", "integer", "Medical staff at the health centre", validation={"min": 0}, **HALF),
             ]},
            {"key": "staffing", "title": "Staffing", "department": False,
             "fields": [
                 _f("academic_staff_total", "integer", "Academic staff (full-time)", required=True,
                    validation={"min": 0}, **HALF),
                 _f("non_academic_staff_total", "integer", "Non-academic staff", required=True,
                    validation={"min": 0}, **HALF),
                 _f("conditions_of_service", YES_NO, "Approved conditions of service (CONPCASS/CONTISS or "
                                                    "equivalent)?", required=True, **HALF),
                 _f("staff_development", "textarea", "Staff development in the last two years"),
             ]},
            {"key": "finance", "title": "Finance", "department": False,
             "fields": [
                 _f("annual_budget", "number", "Approved budget for the current year (₦)", required=True,
                    validation={"min": 0}, **HALF),
                 _f("personnel_cost", "number", "Annual personnel cost (₦)", required=True, validation={"min": 0},
                    **HALF),
                 _f("salary_arrears_months", "integer", "Months of salary arrears outstanding", required=True,
                    validation={"min": 0, "max": 120}, **HALF),
                 _f("funding_sources", "multi_choice", "Sources of funding", required=True,
                    options=["Government subvention", "Proprietor", "Internally generated revenue",
                             "Grants and donors", "TETFund", "Other"]),
                 _f("audited_accounts", YES_NO, "Are last year's accounts audited?", required=True, **HALF),
                 _f("audited_accounts_file", "file", "Audited accounts (last financial year)", required=True),
             ]},
            {"key": "facilities", "title": "Library, ICT and physical facilities", "department": False,
             "fields": [
                 _f("library_seats", "integer", "Library seating capacity", required=True, validation={"min": 0},
                    **HALF),
                 _f("library_volumes", "integer", "Library volumes", required=True, validation={"min": 0}, **HALF),
                 _f("librarian_qualified", YES_NO, "Is the librarian professionally qualified (and registered with "
                                                  "LRCN)?", required=True, **HALF),
                 _f("e_library", YES_NO, "Is there an e-library?", required=True, **HALF),
                 _f("ict_centre", YES_NO, "Is there an ICT / computer centre?", required=True, **HALF),
                 _f("ict_bandwidth_mbps", "number", "Institution internet bandwidth (Mbps)", required=True,
                    validation={"min": 0}, **HALF),
                 _f("ict_erp", YES_NO, "Onboarded on the NBTE Institutional ERP / MIS?", required=True, **HALF),
                 _f("ict_email_domain", "text", "Official e-mail domain in use", required=True, **HALF),
                 _f("power_supply", "multi_choice", "Power supply", required=True,
                    options=["Public power", "Generators", "Solar", "Dedicated transformer"]),
                 _f("water_supply", YES_NO, "Reliable water supply (boreholes / mains)?", required=True, **HALF),
                 _f("security", "textarea", "Security arrangements (fence, gate, guards, CCTV)"),
             ]},
            sec_declaration(),
        ],
    }


# ===========================================================================
# Visit report templates (SOP-03 stage 12): filled by the panel
# ===========================================================================
RATING_OPTIONS = _opts(("very_good", "Very good"), ("good", "Good"), ("fair", "Fair"), ("poor", "Poor"))
RATING_POINTS = {"very_good": 4, "good": 3, "fair": 2, "poor": 1}


def _rated(key, label, *, weight, group, help="", on=None, absent=None, na=None):
    """A Very good / Good / Fair / Poor rating with its comment box. ``absent``
    adds "Absent / does not exist" (0 points); ``na`` adds "Not applicable"
    (excluded from the score, e.g. studios for a programme that has none)."""
    options = list(RATING_OPTIONS)
    points = dict(RATING_POINTS)
    if absent:
        options.append({"value": "absent", "label": absent})
        points["absent"] = 0
    if na:
        options.append({"value": "na", "label": na})
        points["na"] = None
    scoring = {"weight": weight, "group": group, "points": points}
    if on:
        scoring["on"] = on
    rating = _f(key, "choice", label, required=True, options=options, help=help, scoring=scoring, **HALF)
    comment = _f(f"{key}_comment", "textarea", f"{label}: comments",
                 help="Precise and specific: justify the rating, list shortcomings and what to do.")
    return [rating, comment]


PROGRAMME_EVALUATION_GROUPS = {  # key: (label, weight share — documentation only; weights live on the fields)
    "academic": "Academic matters", "employer": "Employers' rating / SIWES", "facilities": "Physical facilities",
    "library": "Library", "staffing": "Staffing", "entrepreneurship": "Entrepreneurship", "funding": "Funding",
}


def programme_evaluation_report():
    """NBTE/P/4A in digital form, one per programme visited. Weights total 100."""
    interim_poor = {"poor": "interim"}
    sections = [
        {"key": "visit", "title": "Visit and verification record", "department": False,
         "description": "Filled from the record; check before scoring. Figures marked 'verified' are what the "
                        "panel counted on site.",
         "fields": [
             _f("programme_title", "text", "Title and level of programme", required=True),
             _f("institution_title", "text", "Institution", required=True),
             _f("visit_dates", "text", "Dates of visit", required=True, **HALF),
             _f("qa_type", "text", "QA exercise", required=True, **HALF),
             _f("ssq_shortfall", "text", "SSQ shortfall score (desk analysis)", **HALF),
             _f("register_items", "text", "NBTE minimum list for the curriculum", **HALF),
             _f("verified_lecturers", "integer", "Core lecturers verified on site", required=True,
                validation={"min": 0}, **HALF),
             _f("verified_technical", "integer", "Technologists / technicians verified", required=True,
                validation={"min": 0}, **HALF),
             _f("verified_enrolment", "integer", "Students enrolled (verified)", required=True,
                validation={"min": 0}, **HALF),
             _f("verified_volumes", "integer", "Library volumes for the programme (verified)", required=True,
                validation={"min": 0}, **HALF),
             _f("verified_classrooms", "integer", "Classrooms available (verified)", validation={"min": 0}, **HALF),
             _f("verified_equipment_pct", "number", "Equipment found against NBTE's list (%)",
                validation={"min": 0, "max": 100}, **HALF,
                help="From 'Facilities and equipment — declared vs found' on the report page."),
         ]},
        {"key": "academic", "title": "I · Academic matters", "department": False,
         "description": "NBTE/P/4A section I. Initial accreditation assesses the first set of graduating students; "
                        "reaccreditation the last two sets.",
         "fields": (
             _rated("goal_objectives", "1.1 Goal and objectives of the programme", weight=2, group="academic")
             + _rated("curriculum", "1.2 The curriculum", weight=5, group="academic",
                      absent="No curriculum", on={"poor": "interim", "absent": "deny"},
                      help="Very good: meets the foundation/core/GNS structure, in course units with behavioural "
                           "objectives, and meets industry and local needs. Poor: substandard.")
             + _rated("admission", "1.3 Admission into the programme", weight=4, group="academic",
                      on=interim_poor, help="Very good: all meet NBTE entry requirements; Good 90–99 %; Fair "
                                            "80–89 %; Poor: more than 20 % do not.")
             + _rated("regulations", "1.4 Academic regulations", weight=2, group="academic", absent="Do not exist",
                      help="Exist, comprehensive, well publicised and applied.")
             + _rated("examinations", "1.4b Standard of tests and examinations, marking schemes", weight=3,
                      group="academic")
             + _rated("student_interview", "1.5 Interview with students", weight=2, group="academic")
             + _rated("success_rate", "1.6 Success rate", weight=3, group="academic", on=interim_poor,
                      na="No examinations yet",
                      help="Very good 90–100 %; Good 80–89 %; Fair 70–79 %; Poor below 70 %.")
             + _rated("practical_coverage", "1.7 Practical coverage and quality of reporting", weight=3,
                      group="academic", on=interim_poor,
                      help="Very good 90–100 % covered and reported; Good 80–89 %; Fair 70–79 %; Poor below 70 %.")
             + _rated("projects", "1.8 Students' projects", weight=1, group="academic", na="No final-year students yet")
             + _rated("grading", "1.9 Grading of practicals and projects", weight=1, group="academic")
             + _rated("moderation", "1.10 External moderation scheme", weight=1, group="academic",
                      na="No final examinations yet")
             + _rated("employer_rating", "1.12 Employers' rating of diplomates / SIWES performance", weight=3,
                      group="employer",
                      help="Institutions with graduates: employers' rating. Others: supervised and graded SIWES."))},
        {"key": "physical", "title": "II · Physical facilities and library", "department": False,
         "description": "NBTE/P/4A section II. The facilities verification against NBTE's minimum list (declared vs "
                        "found) is recorded on the report page; rate it here.",
         "fields": (
             _rated("classrooms", "2.1 Classrooms and lecture theatres", weight=4, group="facilities",
                    on=interim_poor)
             + _rated("studios", "2.2 Studios / drawing rooms", weight=2, group="facilities",
                      absent="Required but not available", na="Not required by the curriculum",
                      on={"poor": "interim", "absent": "interim"})
             + _rated("laboratories", "2.3 Laboratories", weight=6, group="facilities",
                      absent="Required but not available", na="Not required by the curriculum",
                      on={"poor": "interim", "absent": "interim"})
             + _rated("workshops", "2.4 Workshops / field facilities", weight=5, group="facilities",
                      absent="Required but not available", na="Not required by the curriculum",
                      on={"poor": "interim", "absent": "interim"})
             + _rated("equipment", "Equipment against NBTE's minimum list", weight=4, group="facilities",
                      on=interim_poor,
                      help="Suggested: Very good ≥ 90 % of the list found working, Good 80–89 %, Fair 70–79 %, "
                           "Poor below 70 % (see the verified percentage above).")
             + _rated("offices", "2.5 Staff office accommodation", weight=2, group="facilities")
             + _rated("library", "2.6 Library", weight=8, group="library", on=interim_poor,
                      help="Volumes: stream size × 45 titles × 3 volumes; current, diversified, with e-resources."))},
        {"key": "staffing", "title": "III · Staffing", "department": False,
         "fields": (
             _rated("core_staff", "3.1 Core lecturers and instructors", weight=12, group="staffing", on=interim_poor,
                    help="Minimum 4 lecturers per single stream (2 more per extra HND option), pro-rated 1:15 / "
                         "1:20 for more streams; right mix and specialisations.")
             + _rated("service_staff", "3.2 Service lecturers and instructors", weight=3, group="staffing",
                      on=interim_poor)
             + _rated("technical_staff", "3.3 Technologists and technicians", weight=5, group="staffing",
                      on=interim_poor)
             + _rated("admin_staff", "3.4 Administrative staff", weight=2, group="staffing", on=interim_poor)
             + _rated("headship", "3.5 Headship of department", weight=5, group="staffing", on=interim_poor,
                      help="ND: Senior Lecturer or above with 10 years; HND: Principal Lecturer with 13 years; "
                           "registered with the professional body.")
             + _rated("staff_development", "3.6 Staff development", weight=3, group="staffing",
                      absent="No policy"))},
        {"key": "entrepreneurship", "title": "IV · Entrepreneurship development", "department": False,
         "fields": (
             _rated("ent_centre", "4.1 Entrepreneurship Development Centre", weight=2, group="entrepreneurship",
                    absent="Centre not established", on={"poor": "interim", "absent": "interim"})
             + _rated("ent_curriculum", "4.2 Entrepreneurship curriculum", weight=2, group="entrepreneurship",
                      absent="No curriculum", on={"poor": "interim", "absent": "deny"})
             + _rated("ent_projects", "4.3 Entrepreneurship projects", weight=1, group="entrepreneurship",
                      absent="Projects not undertaken", on={"absent": "interim"}))},
        {"key": "funding", "title": "V · Funding of the programme", "department": False,
         "fields": _rated("funding", "5.0 Funding for consumables and imprest", weight=4, group="funding",
                          help="Very good: above ₦10,000 (technology) / ₦5,000 (business) per student a year, "
                               "released when due.")},
        {"key": "summary", "title": "VI · Summary", "department": False,
         "description": "Strengths and weaknesses under each section, and whether the resources meet the stated "
                        "goal and objectives.",
         "fields": [
             _f("strengths", "textarea", "Major strengths", required=True, validation={"min_length": 20}),
             _f("weaknesses", "table", "Weaknesses to correct", min_rows=0,
                columns=[_col("area", "Area", "choice", options=["Academic", "Facilities", "Library", "Staffing",
                                                                 "Entrepreneurship", "Funding", "Other"]),
                         _col("weakness", "Weakness"), _col("action", "Action required"),
                         _col("months", "Within (months)", "integer")]),
         ]},
        {"key": "recommendation", "title": "VII · Recommendation", "department": False,
         "description": "The system computes the recommendation from the ratings (see the score panel). If the "
                        "panel recommends otherwise it must say why.",
         "fields": [
             _f("panel_recommendation", "choice", "The panel's recommendation", required=True,
                options=_opts(("computed", "As computed from the ratings"), ("full", "Full accreditation"),
                              ("interim", "Interim accreditation (deferred)"), ("denied", "Accreditation denied"))),
             _f("override_reason", "textarea", "Reason, where the panel departs from the computed recommendation"),
             _f("conditions", "textarea", "What the institution must do (for interim / denied: the steps to reach "
                                         "the minimum standard)"),
             _f("team", "table", "Team members", required=True, min_rows=1,
                columns=[_col("name", "Name"), _col("role", "Role", "choice",
                                                    options=["Chairman", "Member (academia)",
                                                             "Member (professional body)", "Member (employers)",
                                                             "NBTE coordinator", "ICT officer"]),
                         _col("organisation", "Organisation")]),
         ]},
    ]
    return {
        "form": "NBTE/P/4A",
        "intro": "Programme evaluation by the accreditation panel (NBTE Form NBTE/P/4A, digital). Rate each item "
                 "Very good / Good / Fair / Poor against the guide notes and justify every rating in its comment.",
        "scoring": {
            "method": "rated",
            "groups": PROGRAMME_EVALUATION_GROUPS,
            "full_at": 70, "interim_at": 50, "group_floor": 50,
            "notes": "Score = Σ(weight × points ÷ 4) ÷ Σ(weight of items rated) × 100. Full accreditation: score "
                     "≥ 70 %, every section ≥ 50 % and no deferring observation. Interim: score 50–69 %, a section "
                     "below 50 %, or any observation the NBTE guide notes defer (e.g. curriculum substandard, > 20 % "
                     "admission-deficient, success rate or practical coverage below 70 %, inadequate staff, "
                     "laboratories, workshops or library). Denied: score below 50 %, no curriculum, or no "
                     "entrepreneurship curriculum.",
        },
        "sections": sections,
    }


ICT_COMPONENTS = [  # key, label, weight, threshold for a score of 3
    ("computers", "Computer systems and workstations", 16,
     "Not more than 10 students per working workstation for the programmes verified"),
    ("connectivity", "Internet connectivity, measured", 14,
     "Measured download not below the declared figure, and not below 10 Mbps at the teaching laboratory"),
    ("coverage", "Internet coverage across sites", 10, "Working connectivity at administration, laboratory and library"),
    ("power", "Power supply and backup", 12, "Backup carries the ICT load for a teaching session"),
    ("ict_unit", "ICT department, staffing and governance", 14,
     "A designated head, at least two technical officers and a documented support arrangement"),
    ("lms", "Learning Management System", 10, "Reachable off campus with current content for the programmes"),
    ("elibrary", "e-Library access", 8, "Remote access working, with a current subscription or holding"),
    ("cybersecurity", "Cybersecurity measures", 8, "Firewall and endpoint protection, with evidence of currency"),
    ("backup", "Data backup and recovery", 8, "A backup within seven days and evidence a restore was tested"),
]
ICT_SCORE_OPTIONS = _opts(("4", "4 · Fully meets, accurate declaration"), ("3", "3 · Meets, minor shortfall"),
                          ("2", "2 · Partially meets / overstated > 10 %"), ("1", "1 · Present but inadequate"),
                          ("0", "0 · Absent / not produced"))
ICT_MANDATORY = [
    ("m_website", "Functioning institutional website with current staff and programme information"),
    ("m_email_domain", "Official e-mail domain in use by the Chief Executive and heads of department"),
    ("m_erp", "Subscription to / onboarding on the NBTE Institutional ERP"),
    ("m_dms", "Document management and upload of legacy institutional records"),
    ("m_lms_students", "Learning Management System accessible to enrolled students"),
    ("m_elibrary_remote", "Remote access to the e-library from outside the campus"),
    ("m_logbooks", "Digitised practical log books for the programmes verified"),
    ("m_recorded_practicals", "Recorded practical sessions available for the programmes verified"),
    ("m_geotagged", "Geotagged photographic evidence of equipment, as the policy requires"),
    ("m_ndpc", "Data protection: registered with the NDPC and a data protection officer designated"),
]


def ict_verification_report():
    """NBTE/QA/ICT/VVT/001 version 3.0 (digital): the owner's v2 form with the
    same section letters, scored components (weight × 0–4, /400), the
    mandatory items, capacity arithmetic and an evidence register that every
    score must cite. One per visit (all programmes of the exercise)."""
    score_fields = []
    for key, label, weight, threshold in ICT_COMPONENTS:
        score_fields += [
            _f(f"e_{key}_declared", "text", f"{label} — declared", **HALF,
               help="The SSQ figure; write 'not declared' when there is none (itself a finding)."),
            _f(f"e_{key}_observed", "text", f"{label} — observed", **HALF),
            _f(f"e_{key}", "choice", f"E · {label} (weight {weight})", required=True, options=ICT_SCORE_OPTIONS,
               help=f"Threshold for 3: {threshold}. Between two descriptors award the lower.",
               scoring={"weight": weight, "group": "ict",
                        "points": {"4": 4, "3": 3, "2": 2, "1": 1, "0": 0},
                        "evidence_field": f"e_{key}_evidence"}, **HALF),
            _f(f"e_{key}_evidence", "text", f"{label} — evidence ref(s) (Section K)", required=True,
               placeholder="E03, E04", **HALF),
        ]
    mandatory = []
    for key, label in ICT_MANDATORY:
        mandatory += [
            _f(key, "choice", label, required=True,
               options=_opts(("met", "Met"), ("partly", "Partly met"), ("not_met", "Not met")),
               scoring={"mandatory": True, "on": {"not_met": "not_met"}}, **HALF),
            _f(f"{key}_remark", "text", "Evidence reference and remark", **HALF),
        ]
    sections = [
        {"key": "a_identification", "title": "A · Visit identification", "department": False,
         "description": "Completed from the platform before travel, except the fields marked on site.",
         "fields": [
             _f("institution_name", "text", "Institution name", required=True, **HALF),
             _f("institution_code", "text", "Platform institution ID", required=True, **HALF),
             _f("institution_category", "text", "Institution category", **HALF),
             _f("zone_state", "text", "Zone and State", **HALF),
             _f("batch_reference", "text", "QA batch reference (case)", required=True, **HALF),
             _f("request_type", "text", "Request type", required=True, **HALF),
             _f("visit_date", "date", "Date of visit", required=True, **HALF),
             _f("arrival_departure", "text", "Arrival and departure time (on site)", required=True, **HALF),
             _f("gps_arrival", "text", "GPS coordinates at arrival (on site)", required=True, **HALF),
             _f("gps_registered", "text", "Registered site coordinates (from the platform)", **HALF),
             _f("ict_officer", "text", "ICT officer (name and designation)", required=True, **HALF),
             _f("ict_officer_staff_no", "text", "NBTE staff number", **HALF),
             _f("panel_present", "textarea", "Other panel members present (name, role)"),
             _f("institution_ict_contact", "text", "Institution ICT contact (name, designation)", **HALF),
             _f("institution_ict_phone", "text", "Telephone and official e-mail", **HALF),
             _f("off_site_reason", "textarea", "If arrival was outside the registered site: distance and reason"),
         ]},
        {"key": "b_conflict", "title": "B · Declaration of no conflict of interest", "department": False,
         "description": "Required by NBTE/QA/POL/001 §10 before engagement. Each panel member also declares on the "
                        "report page; a declared conflict results in replacement.",
         "fields": [
             _f("coi_declared", YES_NO, "I declare I have no conflict of interest with this institution (not "
                                       "employed or engaged there in the last three years, no personal or "
                                       "financial interest, no close relative affected, no benefit solicited or "
                                       "accepted).", required=True),
             _f("coi_date", "date", "Date signed", required=True, **HALF),
         ]},
        {"key": "c_desk_review", "title": "C · Pre-visit desk review", "department": False,
         "description": "The declaration you are going to test, taken from the platform before travel. If the "
                        "declared figures cannot be retrieved, stop and refer to the Director, Digital Platforms "
                        "and Analytics.",
         "fields": [
             _f("c_ssq_digital", YES_NO, "Self-study questionnaires submitted digitally", required=True, **HALF),
             _f("c_submission_date", "text", "Date of institution-level submission", **HALF),
             _f("c_programmes", "integer", "Programmes in this exercise", required=True, validation={"min": 0},
                **HALF),
             _f("c_compliance", "text", "Compliance status after SSQ analysis (shortfall scores)", **HALF),
             _f("c_deficiencies", "textarea", "Deficiencies raised at desk review and not yet cleared"),
             _f("c_capacity", "integer", "Approved carrying capacity across the programmes verified (students)",
                required=True, validation={"min": 0}, **HALF),
             _f("c_workstations", "integer", "Workstations declared in the SSQ (units)", required=True,
                validation={"min": 0}, **HALF),
             _f("c_bandwidth", "number", "Internet bandwidth declared (Mbps)", validation={"min": 0}, **HALF),
             _f("c_lms", "text", "Learning Management System declared (name and version)", **HALF),
             _f("c_previous", "text", "Previous ICT verification: date and outcome", **HALF),
             _f("c_outstanding", "textarea", "Outstanding matters from the previous visit"),
         ]},
        {"key": "d_programmes", "title": "D · Programmes under verification", "department": False,
         "description": "One row per programme; Institutional Administration on its own row.",
         "fields": [
             _f("d_programmes", "table", "Programmes", required=True, min_rows=1,
                columns=[_col("programme", "Programme and level"), _col("qa_type", "QA type"),
                         _col("capacity", "Approved capacity", "integer"),
                         _col("workstations", "Declared workstations", "integer"),
                         _col("requirement", "Programme-specific ICT requirement")]),
         ]},
        {"key": "e_scores", "title": "E · Scored assessment of ICT provision", "department": False,
         "description": "Score each component 0–4 against Annex 1. Points = weight × score; maximum 400; "
                        "percentage = points ÷ 4. Every score must cite an evidence reference from Section K.",
         "fields": score_fields},
        {"key": "f_capacity", "title": "F · Capacity arithmetic", "department": False,
         "description": "The figure that decides whether the provision supports the approved intake. A ratio is a "
                        "finding; 'computers available: yes' is not.",
         "fields": [
             _f("f_counted", "integer", "Workstations in academic use, counted on site (excluding administration)",
                required=True, validation={"min": 0}, **HALF),
             _f("f_working", "integer", "Of those, working at the time of the visit", required=True,
                validation={"min": 0}, **HALF),
             _f("f_dedicated", "integer", "Workstations dedicated to the programmes verified",
                validation={"min": 0}, **HALF),
             _f("f_shared", "integer", "Workstations shared with other programmes", validation={"min": 0}, **HALF),
             _f("f_sessions_required", "integer", "Laboratory sessions per week the timetable requires",
                validation={"min": 0}, **HALF),
             _f("f_sessions_supported", "integer", "Laboratory sessions the provision can support",
                validation={"min": 0}, **HALF),
             _f("f_note", "textarea", "Where a ratio cannot be computed, say why (no timetable / no count)"),
         ]},
        {"key": "g_connectivity", "title": "G · Connectivity measurement", "department": False,
         "description": "Measured, not asserted: three named locations at a stated time.",
         "fields": [
             _f("g_readings", "table", "Readings", required=True, min_rows=3,
                columns=[_col("location", "Location"), _col("time", "Time"),
                         _col("download", "Download Mbps", "number"), _col("upload", "Upload Mbps", "number"),
                         _col("latency", "Latency ms", "number"),
                         _col("result", "Result", "choice", options=["pass", "fail", "not measured"])]),
             _f("g_tool", "text", "Measurement tool used", required=True, **HALF),
             _f("g_provider", "text", "Service provider and plan", **HALF),
         ]},
        {"key": "h_platform", "title": "H · Platform engagement", "department": False,
         "description": "Measurable facts about how the institution uses Digital NBTE. Have it perform one routine "
                        "action while you watch.",
         "fields": [
             _f("h_last_signin", "text", "Institution account last sign-in date", **HALF),
             _f("h_accounts", "text", "Programme accounts issued / programmes verified", **HALF),
             _f("h_unchanged_passwords", "integer", "Accounts whose initial password was never changed",
                validation={"min": 0}, **HALF),
             _f("h_ssq_by_department", YES_NO, "SSQs completed by the department (not a consultant)", **HALF),
             _f("h_support_contacts", "integer", "Support contacts logged in the last twelve months",
                validation={"min": 0}, **HALF),
             _f("h_operators", "integer", "Officers who operated the platform unaided (demonstrated)",
                required=True, validation={"min": 0}, **HALF),
             _f("h_domain", YES_NO, "Official institutional domain used for platform correspondence", required=True,
                **HALF),
             _f("h_observed_action", "textarea", "The routine action you watched, and what you observed",
                required=True),
         ]},
        {"key": "i_mandatory", "title": "I · Mandatory compliance items", "department": False,
         "description": "Not scored: met or not met. Any item not met is reported whatever the score at E.",
         "fields": mandatory},
        {"key": "j_sampling", "title": "J · Sampling record", "department": False,
         "description": "At least 10 % of declared units or ten units, whichever is greater, chosen by you.",
         "fields": [
             _f("j_samples", "table", "Assets tested", required=True, min_rows=1,
                columns=[_col("asset", "Asset and location"), _col("model", "Make and model"),
                         _col("serial", "Serial / asset tag"), _col("working", "Powered and working", "yes_no"),
                         _col("evidence_ref", "Evidence ref (Section K)")]),
             _f("j_photographs", "file", "Upload photographs of sampled assets", multiple=True,
                help="Upload JPEG or PNG photographs taken by the panel. Record their evidence references and "
                     "locations in Section K.", validation={"max_files": 20}),
             _f("j_method", "text", "Sampling method (how the units were chosen)", required=True, **HALF),
             _f("j_declared_units", "integer", "Units declared", validation={"min": 0}, **HALF),
         ]},
        {"key": "k_evidence", "title": "K · Evidence register", "department": False,
         "description": "Every photograph and document collected, with location and time, referenced from E, I "
                        "and J. Images supplied by the institution are not evidence of presence.",
         "fields": [
             _f("k_register", "table", "Evidence", required=True, min_rows=1,
                columns=[_col("ref", "Ref (E01…)"), _col("shows", "What it shows"), _col("location", "Location"),
                         _col("time", "Time"), _col("geotag", "Geotag and notes")]),
             _f("k_files_note", "textarea", "Where the files are",
                help="Use this note for documents or evidence stored outside this report form."),
         ]},
        {"key": "l_discrepancies", "title": "L · Discrepancy and deficiency register", "department": False,
         "description": "The principal output: one row per difference between what was declared and what was "
                        "found. Severity per Annex 2; a critical deficiency is reported to the Director the same "
                        "day.",
         "fields": [
             _f("l_register", "table", "Discrepancies and deficiencies",
                columns=[_col("item", "Item"), _col("declared", "Declared"), _col("found", "Found"),
                         _col("severity", "Severity", "choice", options=["Critical", "Major", "Minor"]),
                         _col("action", "Required corrective action"),
                         _col("period_days", "Period (days)", "integer")]),
             _f("l_critical_reported", "date", "Date critical deficiencies were reported to the Director", **HALF),
         ]},
        {"key": "n_observations", "title": "N · ICT observations and recommendation", "department": False,
         "description": "Your professional assessment against the evidence above. A recommendation on the ICT "
                        "component only — never communicated to the institution.",
         "fields": [
             _f("n1_record", "textarea", "N1 · What the platform record showed before the visit", required=True),
             _f("n2_established", "textarea", "N2 · What the visit established (the differences)", required=True,
                validation={"min_length": 20}),
             _f("n3_recommendation", "choice", "N3 · Recommendation on the ICT component", required=True,
                options=_opts(("computed", "As computed (score, mandatory items and deficiencies)"),
                              ("adequate", "ICT provision adequate for the programmes verified"),
                              ("adequate_subject", "Adequate subject to correction of the deficiencies at L"),
                              ("not_adequate_capacity", "Not adequate for the declared carrying capacity"),
                              ("not_adequate", "Not adequate for the programmes verified"),
                              ("obstructed", "Verification obstructed or could not be completed"))),
             _f("n3_reason", "textarea", "Reason (required when obstructed or when departing from the computed "
                                         "recommendation)"),
         ]},
        {"key": "o_acknowledgement", "title": "O · Acknowledgement", "department": False,
         "description": "The institution signs that the visit took place and the factual record was read over. "
                        "Acknowledgement is not agreement, and no outcome is communicated.",
         "fields": [
             _f("o_rep_name", "text", "Institution representative (name and designation)", required=True, **HALF),
             _f("o_signed", YES_NO, "Representative signed the acknowledgement", required=True, **HALF),
             _f("o_refusal", "textarea", "If the institution declined to sign: who, and the reason given"),
         ]},
        {"key": "p_routing", "title": "P · Submission and routing", "department": False,
         "description": "No stage may be held more than 48 hours. Upload before the end of the next working day.",
         "fields": [
             _f("p_uploaded", "date", "Report uploaded with the evidence register", required=True, **HALF),
             _f("p_to_department", "date", "Transmitted to the programme department", **HALF),
             _f("p_cqac", "date", "Received by the CQAC secretariat", **HALF),
         ]},
    ]
    return {
        "form": "NBTE/QA/ICT/VVT/001",
        "intro": "ICT Verification Visit Report, version 3.0 (digital). Issued under NBTE/QA/POL/001 §10 and "
                 "NBTE/QA/SOP/001. Completed by the assigned ICT officer at the institution on the day of the visit; "
                 "it tests what the SSQ declared against what exists on site and records the difference. Retention: "
                 "at least ten years from the determination.",
        "scoring": {
            "method": "ict_vvt",
            "max_points": 400,
            "bands": [[80, "80–100: supports the programmes and the approved carrying capacity"],
                      [60, "60–79: adequate, with named deficiencies to correct within a stated period"],
                      [40, "40–59: material deficiencies; does not support the declared carrying capacity"],
                      [0, "Below 40: does not meet the minimum requirement for the programmes verified"]],
            "notes": "A band of 60 or above does not clear a critical deficiency or an unmet mandatory item; both are "
                     "reported on their own terms. Students per working workstation = approved capacity ÷ working "
                     "units (threshold for a score of 3: not more than 10).",
        },
        "sections": sections,
    }


# ===========================================================================
# Registry
# ===========================================================================
REPORT_HOLDER_TYPES = {
    # holder QA type code: (name, description) — inactive types that hold the
    # report templates so the SSQ builder edits and versions them too
    # (same pattern as the SOP-01 onboarding self-study).
    "report_programme_evaluation": ("Visit report · Programme evaluation (NBTE/P/4A)",
                                    "Panel report template for SOP-03 stage 12 (not a request type)."),
    "report_ict_verification": ("Visit report · ICT verification (NBTE/QA/ICT/VVT/001)",
                                "ICT officer's verification report template (not a request type)."),
}

TEMPLATES = [  # qa type code, template name, schema factory
    ("accreditation", "Accreditation SSQ (NBTE/P/4A criteria)", accreditation_ssq),
    ("reaccreditation", "Reaccreditation SSQ (NBTE/P/4A criteria)", reaccreditation_ssq),
    ("resource_inspection", "Resource Inspection SSQ (new programme)", resource_inspection_ssq),
    ("increase_capacity", "Increase in Carrying Capacity SSQ", increase_capacity_ssq),
    ("validation", "Validation SSQ", validation_ssq),
    ("verification", "Verification SSQ", verification_ssq),
    ("institutional_administration", "Institutional Administration SSQ (Standards)", institutional_administration_ssq),
    ("report_programme_evaluation", "Programme Evaluation Report (NBTE/P/4A)", programme_evaluation_report),
    ("report_ict_verification", "ICT Verification Visit Report (NBTE/QA/ICT/VVT/001 v3)", ict_verification_report),
]


def schema_for(code):
    for type_code, _name, factory in TEMPLATES:
        if type_code == code:
            return copy.deepcopy(factory())
    raise KeyError(code)


def install(*, actor=None, log=None):
    """Create and publish each template as the next version of its QA type,
    when needed (idempotent; a seed-owned template is advanced when its source
    schema changes, while an owner-published version is never replaced).
    Earlier versions are retired by ``builder.publish``; existing SSQs keep
    their version.
    Returns {"created": [...], "unchanged": [...], "errors": {...}}."""
    from django.db.models import Max

    from . import builder
    from . import ssq as engine
    from .models import QAType, SSQTemplate

    out = {"created": [], "unchanged": [], "errors": {}, "types_created": []}
    max_order = QAType.objects.aggregate(m=Max("sort_order"))["m"] or 0
    for i, (code, (name, desc)) in enumerate(REPORT_HOLDER_TYPES.items(), start=1):
        _t, made = QAType.objects.get_or_create(code=code, defaults={
            "name": name, "description": desc, "flow_code": "qa_request", "is_active": False,
            "sort_order": max(max_order, 90) + i * 10})
        if made:
            out["types_created"].append(code)
    for code, name, factory in TEMPLATES:
        qa_type = QAType.objects.filter(code=code).first()
        if qa_type is None:
            out["errors"][code] = "QA type missing (run the qa_core.startup seed first)"
            continue
        schema = factory()
        problems = engine.validate_schema(schema)
        if problems:
            out["errors"][code] = problems[:5]
            continue
        same_name = SSQTemplate.objects.filter(qa_type=qa_type, name=name)
        current = SSQTemplate.objects.filter(qa_type=qa_type, is_current=True).first()
        if current and current.name == name and SEED_TAG in current.notes:
            if current.schema == schema:
                out["unchanged"].append(code)
                continue
        elif same_name.exists():
            out["unchanged"].append(code)
            continue
        version = (SSQTemplate.objects.filter(qa_type=qa_type).aggregate(m=Max("version"))["m"] or 0) + 1
        draft = SSQTemplate.objects.filter(qa_type=qa_type, status=SSQTemplate.DRAFT).first()
        if draft is not None:
            out["errors"][code] = f"version {draft.version} is a draft in the builder; publish or discard it first"
            continue
        template = SSQTemplate.objects.create(
            qa_type=qa_type, version=version, name=name, schema=schema, created_by=actor,
            notes=f"[{SEED_TAG}] Seeded from accreditation/template_library.py (NBTE/P/4A, the accreditation "
                  "standards and NBTE/QA/ICT/VVT/001). Refine in the builder: changes become a new version.")
        builder.publish(template, user=actor, system=True)
        if code in REPORT_HOLDER_TYPES:  # holders never become requestable
            QAType.objects.filter(pk=qa_type.pk).update(is_active=False)
        out["created"].append(code)
        if log:
            log(f"{code}: published v{version} ({len(template.field_map())} fields)")
    return out


# ===========================================================================
# Sample answers (demo data and tests)
# ===========================================================================
def sample_answers(template, *, programme=None, quantities=1.0):
    """Valid answers for every non-file field of ``template`` (catalogue
    quantities at ``quantities`` × required)."""
    from decimal import Decimal

    from . import ssq as engine

    out = {}
    for field in template.field_map().values():
        ftype, key = field["type"], field["key"]
        rules = field.get("validation") or {}
        if ftype == "file":
            continue
        if ftype in ("text", "textarea"):
            out[key] = (f"Sample answer for {field['label'].lower()} — prepared by the department for the "
                        "self-study.")[: int(rules.get("max_length") or 255)]
        elif ftype == "email":
            out[key] = "registrar@sample-poly.edu.ng"
        elif ftype == "phone":
            out[key] = "+234 803 000 0000"
        elif ftype == "url":
            out[key] = "https://lms.sample-poly.edu.ng"
        elif ftype in ("number", "integer"):
            low, high = rules.get("min", 0), rules.get("max")
            value = max(low, 4)
            if high is not None:
                value = min(value, high)
            if key.endswith("year") or key.endswith("_updated") or key == "year_established":
                value = 2023
            if key.endswith("_pct"):
                value = 90
            out[key] = str(int(value))
        elif ftype == "date":
            out[key] = "2024-10-01"
        elif ftype == "choice":
            out[key] = engine.option_pairs(field)[0][0]
        elif ftype == "multi_choice":
            out[key] = [engine.option_pairs(field)[0][0]]
        elif ftype == "yes_no":
            out[key] = "yes"
        elif ftype == "table":
            rows = max(1, int(field.get("min_rows") or 0))
            out[key] = []
            for i in range(rows):
                row = {}
                for col in field.get("columns", []):
                    ctype = col.get("type", "text")
                    if ctype == "choice":
                        opt = col["options"][0]
                        row[col["key"]] = opt.get("value") if isinstance(opt, dict) else opt
                    elif ctype in ("integer", "number"):
                        row[col["key"]] = "5"
                    elif ctype == "yes_no":
                        row[col["key"]] = "yes"
                    elif ctype == "date":
                        row[col["key"]] = "2024-10-01"
                    else:
                        row[col["key"]] = f"{col['label']} {i + 1}"
                out[key].append(row)
        elif ftype == "catalogue":
            qty = {}
            for item in engine.catalogue_items(field, programme):
                have = (Decimal(item.required_quantity) * Decimal(str(quantities))).quantize(Decimal("1"))
                qty[str(item.pk)] = str(have)
            out[key] = qty
    return out
