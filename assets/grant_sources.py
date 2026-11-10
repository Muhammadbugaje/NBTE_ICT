"""Assets as objects of access grants (X5): native visibility for can_see."""
from assignments import registry


def _native(user, obj, scope):
    from .access import can_view_asset_natively
    return can_view_asset_natively(user, obj)


registry.register_native("assets.Asset", _native)
