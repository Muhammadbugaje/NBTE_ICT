"""Demo signatures (generated, fictional) for demo Directors and above - part of
`seed_nbte --with-demo`. Never a real person's signature."""


def demo_signatures(ctx):
    from django.contrib.auth import get_user_model

    from . import signature

    User = get_user_model()
    made = 0
    for user in User.objects.filter(email__iendswith="@demo.nbte.local", is_active=True, user_type="staff"):
        if signature.signature_required(user) and signature.active_for(user) is None:
            signature.save_demo(user)
            made += 1
    if made:
        ctx.res.created += made
        ctx.note(f"{made} generated demo signature(s) for demo Directors")


# v2.1 Phase F (X22, X24): document templates and rules (also created by migration 0003)
from core.seeding import seed_set  # noqa: E402


@seed_set("documents.rules", group="qa", depends=("settings",), order=570, phase="F")
def seed_document_rules(ctx):
    from . import catalogue

    made_t, made_r = catalogue.ensure()
    ctx.res.created += made_t + made_r
    if not (made_t or made_r):
        ctx.res.unchanged += 1
