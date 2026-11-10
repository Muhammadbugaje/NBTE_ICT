"""seed_nbte data sets owned by config (v2.1 Phase A)."""
from core.seeding import seed_set, upsert

DEMO_SITE_ADMIN = "site.admin@demo.nbte.local"
DEMO_PASSWORD = "NBTE-Demo#2026"


@seed_set("brand.logos", group="base", depends=("settings",), order=15, phase="A")
def seed_brand_logos(ctx):
    """The four built-in NBTE marks (static/img/brand/*.svg) for Settings → Branding."""
    from .branding import BRAND_STATIC, BUILTINS
    from .models import BrandLogo

    for order, (code, name, tagline, note, light, dark, aspect, wordmark) in enumerate(BUILTINS):
        upsert(BrandLogo, {"code": code},
               {"name": name, "tagline": tagline, "use_note": note,
                "static_path": f"{BRAND_STATIC}/{light}",
                "static_path_dark": f"{BRAND_STATIC}/{dark}" if dark else "",
                "aspect": aspect, "is_builtin": True, "is_wordmark": wordmark, "order": order}, ctx=ctx)


@seed_set("demo.site_admin", group="demo", depends=("roles",), order=905, phase="A", demo_only=True)
def seed_demo_site_admin(ctx):
    from accounts.models import Role, User
    from staff.models import Department

    if User.objects.filter(email__iexact=DEMO_SITE_ADMIN).exists():
        ctx.res.unchanged += 1
        return
    user = User(email=DEMO_SITE_ADMIN, first_name="Sade", last_name="Site Admin (Demo)", user_type="staff",
                role=Role.objects.filter(code="super-admin").first(),
                department=Department.objects.filter(code__iexact="ict").first(),
                is_active=True, is_staff=False)
    if hasattr(user, "must_change_password"):
        user.must_change_password = False
    user.set_password(DEMO_PASSWORD)
    user.save()
    try:
        from qa_core.models import DemoTag

        DemoTag.tag(user)
    except Exception:  # pragma: no cover - DemoTag is optional here
        pass
    ctx.res.created += 1
    ctx.note(f"{DEMO_SITE_ADMIN} (super-admin) password {DEMO_PASSWORD}")
