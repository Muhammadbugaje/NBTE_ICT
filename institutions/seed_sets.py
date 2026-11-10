"""seed_nbte data sets for v2.1 Phase R (X44).

* ``qa.programme_master``  sample professional-body scopes and programme
  master-list rows (core/seed_data/qa/programme_master.json), all marked
  *to confirm* until NBTE checks them against the official list.

Idempotent; edited rows are kept; scopes are only set on a body that has none.
"""
from core.seeding import load_json, seed_set, upsert


@seed_set("qa.programme_master", group="qa", depends=("qa_core.startup",), files=("qa/programme_master.json",),
          order=570, phase="R")
def seed_programme_master(ctx):
    from .models import Discipline, ProfessionalBody, ProgrammeCategory, ProgrammeLevel, ProgrammeMaster

    data = load_json(ctx, "qa/programme_master.json")
    bodies = {}
    for row in data["bodies"]:
        body = (ProfessionalBody.objects.filter(acronym__iexact=row["acronym"]).first()
                or upsert(ProfessionalBody, {"name": row["name"]}, {"acronym": row["acronym"]}, ctx=ctx))
        bodies[row["acronym"]] = body
        if not (body.scope_disciplines.exists() or body.scope_categories.exists()):
            body.scope_disciplines.set(Discipline.objects.filter(code__in=row["scope_disciplines"]))
            body.scope_categories.set(ProgrammeCategory.objects.filter(name__in=row["scope_categories"]))
            if row.get("requires_letter") and not body.requires_letter_before_commencement:
                body.requires_letter_before_commencement = True
                body.save(update_fields=["requires_letter_before_commencement", "updated_at"])
            ctx.res.filled += 1
    for row in data["rows"]:
        level = ProgrammeLevel.objects.filter(code=row["level"]).first()
        if level is None:
            ctx.warn(f"No level {row['level']}: {row['name']} skipped")
            continue
        cat = ProgrammeCategory.objects.filter(name=row["category"]).first()
        values = {"programme_category": cat, "discipline": cat.discipline if cat else None,
                  "professional_body": bodies.get(row["body"]), "aliases": "\n".join(row.get("aliases", [])),
                  "to_confirm": True, "notes": "Sample row: NBTE to confirm."}
        existing = ProgrammeMaster.objects.filter(name__iexact=row["name"], level=level).first()
        if existing is not None:
            changed = []
            for f, v in values.items():
                if f != "to_confirm" and v and not getattr(existing, f):
                    setattr(existing, f, v)
                    changed.append(f)
            if changed:
                existing.save()
                ctx.res.filled += 1
            else:
                ctx.res.unchanged += 1
            continue
        upsert(ProgrammeMaster, {"name": row["name"], "level": level}, values, ctx=ctx)


@seed_set("qa.body_requirements", group="qa", depends=("qa.programme_master",),
          files=("qa/body_requirements.json",), order=575, phase="R")
def seed_body_requirements(ctx):
    """v2.1 R.6 (X37b): sample requirements per professional body. Idempotent;
    an existing row keeps its text/kind if NBTE edited it."""
    from .models import BodyRequirement, ProfessionalBody, ProgrammeLevel

    data = load_json(ctx, "qa/body_requirements.json")
    for row in data["requirements"]:
        body = ProfessionalBody.objects.filter(acronym__iexact=row["body"]).first()
        if body is None:
            ctx.warn(f"No professional body {row['body']}: {row['code']} skipped")
            continue
        values = {"text": row["text"], "kind": row.get("kind", "other"),
                  "is_mandatory": row.get("is_mandatory", True),
                  "requires_body_letter": row.get("requires_body_letter", False),
                  "sort_order": row.get("sort_order", 0)}
        existing = BodyRequirement.objects.filter(body=body, code=row["code"]).first()
        if existing is not None:
            changed = [f for f, v in values.items() if getattr(existing, f) != v]
            if changed:
                for f in changed:
                    setattr(existing, f, values[f])
                existing.save()
                ctx.res.filled += 1
            else:
                ctx.res.unchanged += 1
            continue
        requirement = BodyRequirement.objects.create(body=body, code=row["code"], **values)
        levels = row.get("levels") or []
        if levels:
            requirement.levels.set(ProgrammeLevel.objects.filter(code__in=levels))
        ctx.res.created += 1


@seed_set("demo.professional_bodies", group="demo", depends=("qa.programme_master",), order=965, phase="R",
          demo_only=True)
def seed_demo_professional_bodies(ctx):
    """Demo PB portal accounts (CPN administrator, PCN viewer) and two demo
    programmes linked to the master list. Only demo-tagged institutions."""
    from django.contrib.contenttypes.models import ContentType

    from accounts.models import Role, User
    from qa_core.models import DemoTag

    from .models import Institution, ProfessionalBody, Programme, ProgrammeMaster

    cpn = ProfessionalBody.objects.filter(acronym__iexact="CPN").first()
    pcn = ProfessionalBody.objects.filter(acronym__iexact="PCN").first()
    if cpn is None:
        ctx.warn("No CPN body: demo professional bodies skipped")
        return
    for email, role, body, first in (("cpn.registrar@demo.nbte.local", "pb-admin", cpn, "Demo CPN"),
                                     ("pcn.viewer@demo.nbte.local", "pb-viewer", pcn, "Demo PCN")):
        if body is None or User.objects.filter(email=email).exists():
            ctx.res.unchanged += 1
            continue
        u = User(email=email, first_name=first, last_name="Registrar" if role == "pb-admin" else "Viewer",
                 user_type="professional_body", professional_body=body, role=Role.objects.filter(code=role).first(),
                 must_change_password=False, is_active=True)
        u.set_password("NBTE-Demo#2026")
        u.save()
        DemoTag.tag(u)
        ctx.res.created += 1
        ctx.note(f"{email} / NBTE-Demo#2026 ({role})")
    ct = ContentType.objects.get_for_model(Institution)
    ids = [int(i) for i in DemoTag.objects.filter(content_type=ct).values_list("object_id", flat=True)]
    cs = ProgrammeMaster.objects.filter(name="Computer Science", level__code="nd").first()
    if cs is None:
        return
    for inst in Institution.unscoped.filter(pk__in=ids).order_by("pk")[:2]:
        p = Programme.unscoped.filter(institution=inst, name__iexact=cs.name, level=cs.level).first()
        if p is None:
            continue
        if p.master_id is None or p.professional_body_id is None:
            p.master, p.professional_body, p.body_source = cs, cs.professional_body, "master"
            p.save(update_fields=["master", "professional_body", "body_source", "updated_at"])
            ctx.res.filled += 1
