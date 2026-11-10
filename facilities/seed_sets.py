"""seed_nbte: the ND/HND facilities and equipment register (v2.2), from the
curricula Word files shipped in core/seed_data/facilities/. Idempotent."""
from core.seeding import seed_set


@seed_set("facilities.register", group="qa", depends=(), files=(), order=900, phase="v2.2")
def seed_register(ctx):
    import io
    import zipfile
    from pathlib import Path

    from django.conf import settings

    from . import docx_parser, services

    path = Path(settings.BASE_DIR) / "core" / "seed_data" / "facilities" / "nbte_nd_hnd_facilities.zip"
    if not path.exists():
        ctx.warn("facilities zip not found; register not loaded")
        return
    parsed = []
    with zipfile.ZipFile(path) as zf:
        for m in zf.namelist():
            if m.lower().endswith(".docx") and "__macosx" not in m.lower():
                parsed.append(docx_parser.parse(io.BytesIO(zf.read(m)), m))
    out = services.load_parsed([p for p in parsed if p["programme"]])
    ctx.note(f"facilities: {out['programmes']} programmes, {out['items']} items added")


@seed_set("facilities.programme_masters", group="qa", depends=("facilities.register",), files=(), order=901,
          phase="v2.3")
def seed_programme_masters(ctx):
    """Every ND/HND curriculum in the register on the programme master list."""
    from . import services

    created, linked = services.import_master_list()
    ctx.res.created += created
    if not created and not linked:
        ctx.res.unchanged += 1
    ctx.note(f"programme master list: {created} added, {linked} linked")
