"""Flow effects for the inspection and post-visit report flows (Phase 5)."""
from qa_core.flow import register_effect

from . import services

_EFFECTS = {
    "inspection_closed": services.on_inspection_closed,
    "report_submitted": services.on_report_submitted,
    "report_returned": services.on_report_returned,
    "report_director_endorsed": services.on_report_director_endorsed,
}
for _name, _fn in _EFFECTS.items():
    register_effect(_name)(_fn)
