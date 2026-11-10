"""v2.2 — render a guide (qa_core.guides_content / staff_guides_content block
format) as safe HTML, for "Read online" previews of every downloadable guide.
Screenshots are shown when the image exists under static/guides/; otherwise
their caption is shown as a note."""
from __future__ import annotations

import re

from django.templatetags.static import static
from django.utils.html import escape
from django.utils.safestring import mark_safe

BOLD = re.compile(r"\*\*(.+?)\*\*")
CODE = re.compile(r"`([^`]+)`")


def _inline(text):
    return CODE.sub(r"<code>\1</code>", BOLD.sub(r"<strong>\1</strong>", escape(text or "")))


def _img(name):
    from pathlib import Path

    from django.conf import settings

    for ext in ("png", "jpg", "webp"):
        rel = f"guides/{name}.{ext}"
        if (Path(settings.BASE_DIR) / "static" / rel).exists():
            return static(rel)
    return ""


def doc_requirement_rows():
    """[document, who uploads it, required?, checked by] rows from the live
    registration rules (the same table the Word / PDF guides print)."""
    rows = []
    try:
        from institutions.models import DocumentRequirement

        qs = (DocumentRequirement.objects.filter(process=DocumentRequirement.PROCESS_REGISTRATION, is_active=True,
                                                 document_kind__is_active=True)
              .select_related("document_kind", "ownership", "reviewing_department")
              .order_by("document_kind__sort_order", "document_kind__name"))
        seen = {}
        for req in qs:
            who = req.ownership.name if req.ownership_id else "Every institution"
            entry = seen.setdefault(req.document_kind_id,
                                    {"name": req.document_kind.name, "who": [], "dept": "", "need": set()})
            if who not in entry["who"]:
                entry["who"].append(who)
            entry["need"].add("Required" if req.is_mandatory else "Optional")
            if req.reviewing_department_id and not entry["dept"]:
                entry["dept"] = req.reviewing_department.name
        for e in seen.values():
            rows.append([e["name"], ", ".join(e["who"]), "Required" if "Required" in e["need"] else "Optional",
                         e["dept"] or "NBTE"])
    except Exception:
        rows = []
    return rows


def render(guide):
    """(toc, html) for one guide dict."""
    out, toc, part_no = [], [], 0
    if guide.get("intro"):
        out.append('<section class="gd-intro">' + "".join(f"<p>{_inline(p)}</p>" for p in guide["intro"])
                   + "</section>")
    if guide.get("before"):
        out.append('<section class="gd-box gd-box--note"><h3>Before you begin</h3><ul class="gd-check">'
                   + "".join(f"<li>{_inline(b)}</li>" for b in guide["before"]) + "</ul></section>")
    for block in guide.get("blocks", []):
        kind = block[0]
        if kind == "part":
            part_no += 1
            anchor = f"part-{part_no}"
            toc.append((anchor, f"{part_no}. {block[1]}"))
            out.append(f'<h2 id="{anchor}" class="gd-part"><span>{part_no}</span>{escape(block[1])}</h2>'
                       + (f'<p class="gd-lead">{_inline(block[2])}</p>' if len(block) > 2 and block[2] else ""))
        elif kind in ("h2", "h3"):
            out.append(f"<h3>{escape(block[1])}</h3>" if kind == "h2" else f"<h4>{escape(block[1])}</h4>")
        elif kind == "p":
            out.append(f"<p>{_inline(block[1])}</p>")
        elif kind == "steps":
            items = []
            for s in block[1]:
                title, _, detail = s.partition("|")
                items.append(f"<li><strong>{_inline(title.strip())}</strong>"
                             + (f"<span>{_inline(detail.strip())}</span>" if detail.strip() else "") + "</li>")
            out.append('<ol class="gd-steps">' + "".join(items) + "</ol>")
        elif kind in ("bullets", "checklist"):
            cls = "gd-check" if kind == "checklist" else ""
            out.append(f'<ul class="{cls}">' + "".join(f"<li>{_inline(b)}</li>" for b in block[1]) + "</ul>")
        elif kind in ("tip", "important", "note"):
            out.append(f'<aside class="gd-box gd-box--{kind}"><strong>{escape(block[1])}</strong>'
                       f"<p>{_inline(block[2])}</p></aside>")
        elif kind == "table":
            head = "".join(f"<th>{escape(h)}</th>" for h in block[1])
            rows = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in block[2])
            out.append(f'<div class="gd-table"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>')
        elif kind in ("shot", "halfshot", "diagram", "phone"):
            src = _img(block[1])
            cap = escape(block[2] if len(block) > 2 else "")
            if src:
                out.append(f'<figure class="gd-fig"><img src="{src}" alt="{cap}" loading="lazy"><figcaption>{cap}'
                           "</figcaption></figure>")
            elif cap:
                out.append(f'<p class="gd-cap">📷 {cap}</p>')
            if kind in ("shot", "halfshot") and len(block) > 3 and block[3]:
                out.append('<ol class="gd-callouts">' + "".join(f"<li value=\"{n}\">{_inline(t)}</li>"
                                                               for n, t in block[3]) + "</ol>")
        elif kind == "phones":
            caps = [escape(c) for _img_name, c in block[1]]
            out.append('<p class="gd-cap">📱 ' + " · ".join(caps) + "</p>")
        elif kind == "doc_requirements":
            rows = doc_requirement_rows()
            if rows:
                head = "".join(f"<th>{h}</th>" for h in ("Document", "Who uploads it", "Required?", "Checked by"))
                body = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in rows)
                out.append(f'<div class="gd-table"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody>'
                           "</table></div>")
            else:
                out.append("<p>The <strong>Documents</strong> page lists exactly which documents apply to your "
                           "institution.</p>")
        elif kind == "help":
            out.append('<aside class="gd-box gd-box--note"><strong>Need help?</strong><p>Choose the <strong>?</strong> '
                       "button at the top of any portal page for help with that page, the guides and NBTE's contact "
                       "details. You can also raise a complaint, or e-mail NBTE (contacts in the footer).</p>"
                       "</aside>")
    return toc, mark_safe("\n".join(out))
