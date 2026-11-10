"""Flow ``complaint`` version 2 (v2.1 X9–X11; Master Plan §13.21).

    received ──acknowledge(director)──► awaiting_assignment ──assign(director)──► in_progress
    in_progress ──submit_for_review(assigned officer)──► director_review
    director_review ──resolve(director)──► resolved          (institution notified)
    director_review ──return_to_officer(director, remark)──► in_progress
    in_progress ──request_info(officer)──► awaiting_raiser ──raiser_replied──► in_progress
    in_progress / director_review ──refer(director)──► referred ──assign(that director)──► in_progress
    resolved ──confirm | auto_close──► closed ; resolved ──reopen──► in_progress (same officer)
    received / awaiting_assignment ──close(director, remark)──► closed

Seeded as a new FlowDefinition version beside v1: complaints already running
stay pinned to v1 (the flow engine pins instances to their version); new
complaints start on v2. ``seed()`` is idempotent: once any version 2+ of the
``complaint`` flow exists it does nothing (published flows change only by
versioning, never by re-seeding).

Who may take each step is decided in ``complaints.services`` (the director
seat of the handling department, the assigned officer); the transitions only
carry the permission and the remark rule.
"""
from __future__ import annotations

CODE = "complaint"
VERSION = 2

OFFICER = {"required_permission": "complaints.handle_complaint"}
OFFICER_R = {"required_permission": "complaints.handle_complaint", "requires_remark": True}
DIRECTOR = {"required_permission": "complaints.close_complaint"}
DIRECTOR_R = {"required_permission": "complaints.close_complaint", "requires_remark": True}
TASK = {"creates_task": True, "sla_business_hours": True, "task_category": "qa-complaint"}

# Stages whose task is the handling director's; the officer's stage is in_progress.
DIRECTOR_STAGES = ("received", "awaiting_assignment", "director_review", "referred")
OFFICER_STAGES = ("in_progress",)
# Actions the handling director (or a delegate, the ES, super admin) takes.
DIRECTOR_ACTIONS = ("acknowledge", "assign", "resolve", "return_to_officer", "refer", "close")
# Actions only the assigned officer takes.
OFFICER_ACTIONS = ("submit_for_review", "request_info", "resume")

SPEC = {
    "code": CODE,
    "name": "Complaint (director assigns, officer submits for review)",
    "subject_model": "complaints.complaint",
    "description": "v2.1 X9–X11: the category's handling department director assigns one of his own staff, "
                   "who submits for review; only the director resolves, returns, refers or closes.",
    "stages": [
        ("received", "Received — director to acknowledge", "Received", "start", "role_in_department",
         dict(TASK, sla_hours=8)),
        ("awaiting_assignment", "Awaiting assignment", "Acknowledged", "action", "role_in_department",
         dict(TASK, sla_hours=8)),
        ("in_progress", "Being worked on", "Being worked on", "action", "role_in_department",
         dict(TASK, sla_hours=40)),
        ("director_review", "Ready for the director's review", "Being worked on", "action", "role_in_department",
         dict(TASK, sla_hours=16)),
        ("awaiting_raiser", "Waiting for the raiser", "Waiting for your reply", "action", "institution",
         {"pauses_sla": True}),
        ("referred", "Referred to another department", "Being worked on", "action", "role_in_department",
         dict(TASK, sla_hours=24)),
        ("resolved", "Resolved — awaiting confirmation", "Resolved — please confirm", "action", "institution", {}),
        ("closed", "Closed", "Closed", "end", "system", {"is_terminal_success": True}),
        ("withdrawn", "Withdrawn", "Withdrawn", "end", "system", {"is_terminal_failure": True}),
    ],
    "transitions": [
        ("received", "awaiting_assignment", "acknowledge", "Acknowledge", DIRECTOR),
        ("received", "in_progress", "assign", "Assign to an officer", DIRECTOR),
        ("received", "closed", "close", "Close (duplicate or out of scope)", DIRECTOR_R),
        ("awaiting_assignment", "in_progress", "assign", "Assign to an officer", DIRECTOR),
        ("awaiting_assignment", "closed", "close", "Close (duplicate or out of scope)", DIRECTOR_R),
        ("in_progress", "director_review", "submit_for_review", "Submit for review", OFFICER_R),
        ("in_progress", "awaiting_raiser", "request_info", "Request information from the raiser", OFFICER_R),
        ("in_progress", "referred", "refer", "Refer to another department", DIRECTOR_R),
        ("director_review", "resolved", "resolve", "Resolve", DIRECTOR_R),
        ("director_review", "in_progress", "return_to_officer", "Return to the officer", DIRECTOR_R),
        ("director_review", "referred", "refer", "Refer to another department", DIRECTOR_R),
        ("referred", "in_progress", "assign", "Assign to an officer", DIRECTOR),
        ("awaiting_raiser", "in_progress", "raiser_replied", "Raiser replied", {}),
        ("awaiting_raiser", "in_progress", "resume", "Resume without a reply", OFFICER_R),
        ("awaiting_raiser", "closed", "close", "Close (no reply)", DIRECTOR_R),
        ("resolved", "closed", "confirm", "Confirm it is resolved", {}),
        ("resolved", "closed", "auto_close", "Close automatically", {}),
        ("resolved", "in_progress", "reopen", "Reopen", {}),
        ("received", "withdrawn", "withdraw", "Withdraw the complaint", {}),
        ("awaiting_assignment", "withdrawn", "withdraw", "Withdraw the complaint", {}),
        ("in_progress", "withdrawn", "withdraw", "Withdraw the complaint", {}),
        ("director_review", "withdrawn", "withdraw", "Withdraw the complaint", {}),
        ("awaiting_raiser", "withdrawn", "withdraw", "Withdraw the complaint", {}),
        ("referred", "withdrawn", "withdraw", "Withdraw the complaint", {}),
    ],
}


def is_directed(flow_version):
    """True for a flow built on the director / officer split (has a director_review stage)."""
    return flow_version is not None and flow_version.stages.filter(code="director_review").exists()


def seed():
    """Create version 2 of the complaint flow once. Returns (created, stages, transitions)."""
    from django.db import transaction
    from django.utils import timezone

    from qa_core.models import FlowDefinition, FlowStage, FlowTransition
    from tasks.models import TaskCategory

    if not FlowDefinition.objects.filter(code=CODE).exists():
        return 0, 0, 0  # v1 is seeded first by seed_startup._flows
    if FlowDefinition.objects.filter(code=CODE, version__gte=VERSION).exists():
        return 0, 0, 0
    with transaction.atomic():
        flow = FlowDefinition.objects.create(code=CODE, name=SPEC["name"], description=SPEC["description"],
                                             subject_model=SPEC["subject_model"], version=VERSION, is_active=True,
                                             published_at=timezone.now())
        by_code = {}
        for order, (code, label, public, kind, actor_kind, extra) in enumerate(SPEC["stages"]):
            extra = dict(extra)
            if "task_category" in extra:
                extra["task_category"] = TaskCategory.objects.filter(code=extra["task_category"]).first()
            by_code[code] = FlowStage.objects.create(flow=flow, code=code, label=label, public_label=public,
                                                     kind=kind, actor_kind=actor_kind, sort_order=(order + 1) * 10,
                                                     **extra)
        for order, (src, dst, action, label, extra) in enumerate(SPEC["transitions"]):
            FlowTransition.objects.create(flow=flow, from_stage=by_code[src], to_stage=by_code[dst],
                                          action_code=action, label=label, sort_order=(order + 1) * 10, **extra)
    return 1, len(SPEC["stages"]), len(SPEC["transitions"])
