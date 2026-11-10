"""Hero text from Settings → Homepage (v2.1 A8).

`hero_context("home")` / `hero_context("portal.home")` read the
`<prefix>.hero.*` keys with fallbacks to the built-in wording, so a missing or
blank row never empties the hero. Text is plain (auto-escaped in templates);
{department} and {organisation} are replaced with the site names.
"""
from config.defaults import HOME_TEXT_DEFAULTS, DEFAULTS

SCENES = ("laminar", "classic", "none")
_TYPED_DEFAULTS = {k: (v, t) for k, v, t, _g, _d in DEFAULTS}


def _get(key, default=None):
    try:
        import config

        return config.get(key, default)
    except Exception:
        return default


def _text(key, names):
    default = HOME_TEXT_DEFAULTS.get(key, "")
    value = str(_get(key, default) or "").strip()
    value = value or default
    for token, name in names.items():
        value = value.replace("{" + token + "}", name)
    return value


def _typed(key):
    import json

    raw, vtype = _TYPED_DEFAULTS.get(key, ("", "str"))
    if vtype == "json":
        default = json.loads(raw or "null")
    elif vtype == "bool":
        default = raw == "true"
    else:
        default = raw
    value = _get(key, default)
    if vtype == "json" and isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = default
    return value


def _stats(items):
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            value = int(item.get("value"))
        except (TypeError, ValueError):
            continue
        plain = bool(item.get("plain"))
        out.append({"value": value, "display": str(value) if plain else f"{value:,}",
                    "suffix": str(item.get("suffix") or "")[:4], "label": str(item.get("label") or "")[:80],
                    "plain": plain})
    return out[:6]


def hero_context(prefix="home", site_info=None):
    site_info = site_info or {}
    names = {"department": site_info.get("department_name", "") or "ICT Directorate",
             "organisation": site_info.get("organisation_name", "") or "National Board for Technical Education"}
    keys = [k for k in HOME_TEXT_DEFAULTS if k.startswith(f"{prefix}.hero.")]
    hero = {k.rsplit(".", 1)[1]: _text(k, names) for k in keys}
    ticker = _typed(f"{prefix}.hero.ticker")
    hero["ticker"] = [str(w)[:40] for w in ticker][:8] if isinstance(ticker, list) else []
    hero["stats"] = _stats(_typed(f"{prefix}.hero.stats")) if f"{prefix}.hero.stats" in _TYPED_DEFAULTS else []
    crest = _typed(f"{prefix}.hero.crest")
    hero["crest"] = crest if isinstance(crest, bool) else str(crest).lower() in {"1", "true", "yes", "on"}
    if hero.get("scene") not in SCENES:
        hero["scene"] = "laminar"
    return hero


def history_enabled():
    value = _typed("home.history.enabled")
    return value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "on"}
