"""seed_nbte data sets owned by tickets (v2.1 Phase C).

* ``tickets.funds``  — the workflow request type behind "Request funds" on a
  ticket (Finance director approval -> ES approval -> Finance releases).
* ``demo.phase_c``   — demo only: an ICT division with a head, shared and
  department assets, a ticket routed to the division with a funds request and
  temporary asset access, a resolved ticket waiting for its rating, a closed
  ticket and a closed request. Idempotent (each step runs only if missing).
"""
from core.seeding import seed_set, upsert

FUNDS_CODE = "ticket-funds"


@seed_set("tickets.funds", group="base", depends=("roles", "org.departments"), order=480, phase="C")
def seed_ticket_funds(ctx):
    from accounts.models import Role
    from staff.models import Department
    from workflow.models import ApprovalChain, ApprovalStep, RequestCategory, RequestType

    from .bridge import FUNDS_TYPE_DEFAULTS

    finance = Department.objects.filter(code="finance").first()
    if finance is None:
        ctx.warn("No 'finance' department: the funds request type is created on first use instead.")
        return
    cat = upsert(RequestCategory, {"code": "funds"}, {"name": "Funds", "sort_order": 90}, ctx=ctx)
    chain = upsert(ApprovalChain, {"code": "ticket-funds-release"},
                   {"name": "Ticket funds: Finance releases",
                    "description": "After Finance and ES approval, Finance releases."}, ctx=ctx)
    upsert(ApprovalStep, {"chain": chain, "position": 1},
           {"name": "Finance releases the funds", "kind": ApprovalStep.KIND_APPROVAL,
            "approver_kind": ApprovalStep.APPROVER_RECEIVING_DIRECTOR, "scope": "receiving",
            "is_hierarchical": False, "approver_role": Role.objects.filter(code="director").first(),
            "instructions": "Release the approved amount to the ticket's handler, then approve this step."}, ctx=ctx)
    upsert(RequestType, {"code": FUNDS_CODE},
           {**FUNDS_TYPE_DEFAULTS, "category": cat, "handling_department": finance, "approval_chain": chain,
            "requires_finance_approval": True, "requires_es_approval": True, "is_active": True,
            "sort_order": 900, "icon": "banknote"}, ctx=ctx)


DEMO = {
    "director": "qa.ict_director@demo.nbte.local",
    "head": "qa.ict_officer2@demo.nbte.local",
    "member": "qa.ict_officer@demo.nbte.local",
    "fin_officer": "qa.finance_officer@demo.nbte.local",
    "fin_clerk": "finance.clearance@demo.nbte.local",
    "fin_director": "qa.finance_director@demo.nbte.local",
    "qa_officer": "qa.officer@demo.nbte.local",
}


def _tag(obj):
    try:
        from qa_core.models import DemoTag
        DemoTag.tag(obj)
    except Exception:  # pragma: no cover
        pass


@seed_set("demo.phase_c", group="demo", depends=("demo", "tickets.funds"), order=930, phase="C", demo_only=True)
def seed_demo_phase_c(ctx):
    import org
    from accounts.models import User
    from assets import services as asvc
    from assets.models import Asset, AssetCategory
    from org.models import Division
    from staff.models import Department
    from workflow import services as wsvc
    from workflow.models import Request, RequestType

    from . import bridge, routing
    from . import services as tsvc
    from .models import Ticket, TicketCategory

    users = {k: User.objects.filter(email__iexact=v, is_active=True).first() for k, v in DEMO.items()}
    if any(u is None for u in users.values()):
        ctx.warn("Phase C demo skipped: a demo account is missing (" +
                 ", ".join(DEMO[k] for k, u in users.items() if u is None) + ").")
        return
    u = users
    ict = Department.objects.filter(code="ict").first()
    fin = Department.objects.filter(code="finance").first()
    hw = TicketCategory.objects.filter(code="hardware").first()
    sw = TicketCategory.objects.filter(code="software").first()
    printer_cat = AssetCategory.objects.filter(code__icontains="print").first() or AssetCategory.objects.first()
    if not (ict and fin and hw and sw and printer_cat):
        ctx.warn("Phase C demo skipped: ICT/Finance departments, ticket categories or asset categories missing.")
        return

    def done(created):
        if created:
            ctx.res.created += 1
        else:
            ctx.res.unchanged += 1

    # 1. ICT division with a head and a member
    div = Division.objects.filter(department=ict, name="Technical Support (Demo)").first()
    done(div is None)
    if div is None:
        div = org.create_division(ict, "Technical Support (Demo)", None, max_open_tasks=5)
        _tag(div)
    if div.head_id != u["head"].pk:
        org.set_division_head(div, u["head"], None)
    member_staff = getattr(u["member"], "staff_profile", None)
    if member_staff is not None and member_staff.division_id != div.pk:
        org.move_to_division(member_staff, div, None)

    # 2. Assets: individual printer, shared scanner, department kettle
    def asset(tag, mode, **fields):
        a = Asset.objects.filter(asset_tag=tag).first()
        done(a is None)
        if a is None:
            a = asvc.create_asset(category=printer_cat, owner_department=fin, asset_tag=tag, state="in_service", **fields)
            _tag(a)
        if a.assignment_mode != mode and not a.open_assignments().exists():
            asvc.set_assignment_mode(asset=a, mode=mode, by_user=u["fin_director"])
        return a

    printer = asset("DEMO-PRN-0001", Asset.MODE_INDIVIDUAL, brand="HP", model="LaserJet Pro M404 (Demo)",
                    current_location="Finance, Block A room 4")
    if not printer.open_assignments().exists():
        asvc.assign_asset(asset=printer, to_user=u["fin_officer"], by_user=u["fin_director"])
    if not printer.issues.exists():
        issue = asvc.report_issue(asset=printer, reported_by=u["fin_officer"], summary="Paper jam on tray 2 (demo)")
        asvc.resolve_issue(issue=issue, resolved_by=u["member"], resolution_notes="Roller cleaned.")
    scanner = asset("DEMO-SCN-0001", Asset.MODE_SHARED, brand="Canon", model="imageFORMULA DR-C225 (Demo)",
                    current_location="Finance, open office")
    for person in (u["fin_officer"], u["fin_clerk"]):
        if not scanner.open_assignments().filter(assigned_to=person).exists():
            asvc.assign_asset(asset=scanner, to_user=person, by_user=u["fin_director"])
    kettle = asset("DEMO-KTL-0001", Asset.MODE_DEPARTMENT, brand="Binatone", model="Electric kettle (Demo)",
                   current_location="Finance kitchenette")
    if not kettle.open_assignments().exists():
        asvc.assign_asset(asset=kettle, department=fin, by_user=u["fin_director"])

    # 3. Ticket routed to the division, accepted, assigned, funds requested
    t = Ticket.objects.filter(subject="Demo: Finance printer prints blank pages").first()
    done(t is None)
    if t is None:
        t = tsvc.create_ticket(raised_by=u["fin_officer"], category=hw, priority="high", asset=printer,
                               subject="Demo: Finance printer prints blank pages",
                               body="The shared printer in Finance prints blank pages since Monday. "
                                    "Toner was replaced last week.", current_location="Finance, Block A room 4")
        _tag(t)
    if not t.division_id and not t.activities.filter(kind="routed_division").exists():
        routing.route_to_division(t, u["director"], div, "Printer fault for Technical Support.")
    t.refresh_from_db()
    if t.division_state == Ticket.DIV_PENDING:
        routing.accept_division(t, u["head"])
    t.refresh_from_db()
    if not t.assigned_to_id:
        tsvc.assign_ticket(t, u["head"], u["member"], reason="Please look today.")
    t.refresh_from_db()
    if t.state == Ticket.ASSIGNED:
        tsvc.start_work(t, u["member"], note="Checking the fuser unit.")
        tsvc.add_public_comment(t, u["member"], "The fuser unit looks worn; a replacement is needed.")
    t.refresh_from_db()
    if not t.funds_state and t.state == Ticket.IN_PROGRESS:
        bridge.request_funds(t, u["member"], "45000", "Replacement fuser unit for the Finance printer (HP RM2-5425).")

    # 4. Resolved ticket waiting for its rating
    t2 = Ticket.objects.filter(subject="Demo: Outlook will not sync").first()
    done(t2 is None)
    if t2 is None:
        t2 = tsvc.create_ticket(raised_by=u["qa_officer"], category=sw, subject="Demo: Outlook will not sync",
                                body="Mail stopped syncing this morning.", priority="normal")
        _tag(t2)
        tsvc.assign_ticket(t2, u["director"], u["head"])
        tsvc.start_work(t2, u["head"])
        tsvc.resolve_ticket(t2, u["head"], resolution_notes="Profile rebuilt; mail syncing again.")

    # 5. Closed and rated ticket (read-only record)
    t3 = Ticket.objects.filter(subject="Demo: Projector bulb in the board room").first()
    done(t3 is None)
    if t3 is None:
        t3 = tsvc.create_ticket(raised_by=u["qa_officer"], category=hw, priority="normal",
                                subject="Demo: Projector bulb in the board room",
                                body="The projector shows a lamp warning and switches off after ten minutes.")
        _tag(t3)
        tsvc.assign_ticket(t3, u["director"], u["member"])
        tsvc.start_work(t3, u["member"])
        tsvc.add_public_comment(t3, u["member"], "Bulb ordered from stores.")
        tsvc.resolve_ticket(t3, u["member"], resolution_notes="Lamp replaced and hours counter reset.")
        tsvc.close_ticket(t3, u["qa_officer"], rating=4, comment="Quick and tidy, thank you.")

    # 6. A ticket waiting for the division head to accept
    t4 = Ticket.objects.filter(subject="Demo: Scanner feeds two sheets at once").first()
    done(t4 is None)
    if t4 is None:
        t4 = tsvc.create_ticket(raised_by=u["fin_clerk"], category=hw, priority="normal", asset=scanner,
                                subject="Demo: Scanner feeds two sheets at once",
                                body="The shared scanner pulls two pages together and the PDF misses pages.")
        _tag(t4)
        routing.route_to_division(t4, u["director"], div, "Scanner rollers, please check.")

    # 7. Closed request (read-only)
    r = Request.objects.filter(subject="Demo: toner for DPAP printer").first()
    done(r is None)
    rt = RequestType.objects.filter(code="ict-printer").first()
    if r is None and rt is not None:
        r = wsvc.raise_request(requester=u["qa_officer"], request_type=rt, subject="Demo: toner for DPAP printer",
                               body="We need two toner cartridges.")
        _tag(r)
        wsvc.add_comment(r, u["qa_officer"], "Model is HP 59A.")
        wsvc.cancel_request(r, u["qa_officer"], "Bought locally (demo).")
    ctx.note("Phase C demo: ICT division 'Technical Support (Demo)' (head qa.ict_officer2), printer ticket with a "
             "funds request, resolved ticket to rate (qa.officer), shared scanner and department kettle in Finance.")
