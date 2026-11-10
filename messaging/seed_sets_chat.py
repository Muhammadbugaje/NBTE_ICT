"""seed_nbte data sets owned by messaging (v2.1 Phase E, chat part).

* ``demo.phase_e_chat`` - demo only: a long group chat between demo staff
  ("EXCO Secretariat (Demo)") with an owner, two admins, a poll, a pinned
  message, a reminder and an invite link, so the sticky back bar, the group
  info sheet, members management, pins and reminders can be tried at once.
  Idempotent: every step runs only when its row is missing.
"""
from datetime import timedelta

from core.seeding import seed_set

DEMO_CHAT_TITLE = "EXCO Secretariat (Demo)"
DEMO_CHAT = {
    "owner": "qa.director@demo.nbte.local",
    "admin": "qa.deputy_director@demo.nbte.local",
    "admin2": "qa.es@demo.nbte.local",
    "m1": "qa.officer@demo.nbte.local",
    "m2": "qa.division_head@demo.nbte.local",
    "m3": "qa.team_lead@demo.nbte.local",
    "m4": "qa.ict_director@demo.nbte.local",
    "m5": "qa.finance_director@demo.nbte.local",
}
# Who says what (key into DEMO_CHAT, text); repeated to give a long thread.
DEMO_LINES = [
    ("owner", "Good morning all. This group is for the EXCO secretariat work this week."),
    ("admin", "Morning. The EXCO pack goes out on Wednesday; please send your inputs by Tuesday noon."),
    ("m1", "Noted. The accreditation summary is almost ready."),
    ("m2", "Division reports are in the shared folder."),
    ("admin2", "Thank you. Please keep each submission to two pages."),
    ("m3", "Will the meeting hold in the main boardroom?"),
    ("owner", "Yes, unless the ES conference room is free. I'll confirm."),
    ("m4", "The projector in the boardroom has been serviced."),
    ("m5", "Finance figures will be attached as an annex."),
    ("m1", "Please who is collating the minutes of the last meeting?"),
    ("admin", "Tani has the draft minutes."),
    ("m2", "Draft minutes shared. Comments by tomorrow please."),
    ("owner", "Thanks everyone. Let's keep this moving."),
]


def _tag(obj):
    try:
        from qa_core.models import DemoTag
        DemoTag.tag(obj)
    except Exception:  # pragma: no cover
        pass


def demo_chat_users():
    from accounts.models import User

    return {k: User.objects.filter(email__iexact=v, is_active=True).first() for k, v in DEMO_CHAT.items()}


def ensure_demo_chat_base(users=None):
    """The demo group thread with its messages and a poll. Returns (thread, created)."""
    from django.utils import timezone

    from . import chat_services as cs
    from .models import Message, Thread, ThreadParticipant

    users = users or demo_chat_users()
    owner = users["owner"]
    thread = Thread.objects.filter(subject=DEMO_CHAT_TITLE, created_by=owner).first()
    if thread is not None:
        return thread, False
    start = timezone.now() - timedelta(hours=6)
    thread = Thread.objects.create(subject=DEMO_CHAT_TITLE, thread_type=Thread.GROUP, created_by=owner)
    _tag(thread)
    for u in users.values():
        ThreadParticipant.objects.get_or_create(thread=thread, user=u)
    at = start
    for rnd in range(3):
        for key, text in DEMO_LINES:
            at += timedelta(minutes=4)
            Message.objects.create(thread=thread, sender=users[key], body=text, sent_at=at)
    cs.create_poll(thread, users["admin"], question="Where should EXCO meet?",
                   options=["Main boardroom", "ES conference room"])
    thread.last_message_at = timezone.now()
    thread.save(update_fields=["last_message_at"])
    return thread, True


@seed_set("demo.phase_e_chat", group="demo", depends=("demo",), order=950, phase="E", demo_only=True)
def seed_demo_phase_e_chat(ctx):
    from django.utils import timezone

    from . import group_services as gs
    from .models import MessageReminder, ThreadInvite, ThreadParticipant

    users = demo_chat_users()
    missing = [DEMO_CHAT[k] for k, u in users.items() if u is None]
    if missing:
        ctx.warn("Phase E chat demo skipped: demo account(s) missing: " + ", ".join(missing))
        return
    thread, created = ensure_demo_chat_base(users)
    ctx.res.created += int(created)
    ctx.res.unchanged += int(not created)

    # Roles: the director owns it; the deputy director and the ES are admins.
    roles = {users["owner"].pk: ThreadParticipant.OWNER, users["admin"].pk: ThreadParticipant.ADMIN,
             users["admin2"].pk: ThreadParticipant.ADMIN}
    for p in thread.participants.all():
        want = roles.get(p.user_id, ThreadParticipant.MEMBER)
        if p.role != want:
            p.role = want
            p.save(update_fields=["role"])
            ctx.res.filled += 1
    if not thread.description:
        thread.description = "Coordination of the EXCO pack, minutes and meeting logistics (demo)."
        thread.save(update_fields=["description", "updated_at"])
        ctx.res.filled += 1

    # One pinned message (the deadline) and one reminder for the owner.
    deadline = thread.messages.filter(body__startswith="Morning. The EXCO pack").order_by("id").first()
    if deadline is not None and not thread.messages.filter(pinned_at__isnull=False).exists():
        gs.pin_message(deadline, users["admin"], True)
        ctx.res.created += 1
    mine = thread.messages.filter(sender=users["owner"], is_system=False).order_by("-id").first()
    if mine is not None and not MessageReminder.objects.filter(user=users["owner"], message__thread=thread).exists():
        MessageReminder.objects.create(user=users["owner"], message=mine,
                                       remind_at=timezone.now() + timedelta(days=1))
        ctx.res.created += 1
    if not ThreadInvite.objects.filter(thread=thread).exists():
        gs.create_invite(thread, users["owner"], days=7)
        ctx.res.created += 1
