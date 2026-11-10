"""Organisation rules (Phase B). Use as ``import org; org.director_of(dept)``.

Everything public lives in ``org.services`` and is re-exported here; models
are imported lazily inside those functions, so importing ``org`` is safe
before the app registry is ready. API notes: docs/master_plan_v2_1/notes/org_api.md.
"""
from .services import *  # noqa: F401,F403
from .services import __all__  # noqa: F401
