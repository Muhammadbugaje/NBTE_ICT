"""`config.get(key, default=None, scope='global')` - see config.registry.

Department-specific behaviour: a row with scope `department:<code>` overrides the
global value for people in that department; everyone else gets the global one.
    config.get_for_user("approval.levels", request.user, 4)
    config.get_for_department("approval.levels", dept, 4)
"""


def get(key, default=None, scope="global"):
    from .registry import get as _get

    return _get(key, default, scope)


def get_for_department(key, department, default=None):
    scope = department.config_scope if department is not None else "global"
    return get(key, default, scope)


def get_for_user(key, user, default=None):
    return get(key, default, getattr(user, "config_scope", "global"))
