"""Shared selectors for the guided-tour registries (qa_core/tours.py and the
tours_*.py modules it gathers). Kept apart so every registry can import them."""

MENU = ".pp-tabbar [data-pp-menu]"  # the portal phone tab bar's "Menu" button (opens the sidebar drawer)
STAFF_MENU = "[data-drawer-toggle='app-sidebar']"  # the staff shell's menu button
LAUNCHER = ["[data-nt-launcher] .nt-launch__btn"]  # the floating "Guide me" button
PB_HOME = r"^/(pb|dashboard)/$"


def nav(url):
    """A link in the portal side menu."""
    return [f".pp-nav a[data-qa-nav='{url}']"]


def staff_nav(url):
    """A link in the staff side menu (main system or QA workspace)."""
    return [f".app-sidebar a.app-nav-item[href='{url}']"]
