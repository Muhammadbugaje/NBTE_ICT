"""Read NBTE "List of Minimum Resources / Physical Facilities and Equipment"
Word files (one per programme, as extracted from the NBTE curricula register).

Layout of each file::

    NATIONAL BOARD FOR TECHNICAL EDUCATION
    <PROGRAMME NAME>
    LIST OF MINIMUM RESOURCES / PHYSICAL FACILITIES AND EQUIPMENT
    National Diploma (ND) [and Higher National Diploma (HND)]
    Source curricula (NBTE Register): …
    A. <FACILITY / LABORATORY / WORKSHOP NAME>
    | S/N | Description of Items | Quantity |            (one level)
    | S/N | Description of Items | ND Qty | HND Qty |     (both levels)
    B. …
    Notes …

``parse(file)`` returns a dict ``{programme, levels, source, notes, sections:
[{code, name, items: [{sn, name, nd, hnd, remarks}]}]}``. Quantities are kept
as written ("5", "1 set", "-"); ``qty_number`` turns them into numbers.
"""
from __future__ import annotations

import re

SECTION_RE = re.compile(r"^([A-Z]{1,2})\.\s+(.+)$")
SKIP_TITLES = ("NATIONAL BOARD FOR TECHNICAL EDUCATION", "LIST OF MINIMUM RESOURCES")


def _body(fileobj):
    """The <w:body> of a .docx (path or file object), read with plain lxml:
    much faster than python-docx's proxy objects, and with entity resolution
    and network access switched off (the files come from uploads)."""
    import zipfile

    from lxml import etree

    with zipfile.ZipFile(fileobj) as zf:
        info = zf.getinfo("word/document.xml")
        if info.file_size > 200 * 1024 * 1024:
            raise ValueError("document.xml is too large")
        xml = zf.read(info)
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, remove_comments=True)
    root = etree.fromstring(xml, parser)
    body = root.find(_W + "body")
    return body if body is not None else root


def _para_text(p):
    parts = []
    for el in p.iter(_W + "t", _W + "tab", _W + "br", _W + "cr"):
        parts.append((el.text or "") if el.tag == _W + "t" else " ")
    return "".join(parts)


def _iter_blocks(body):
    """('p', text) and ('tbl', rows) in document order (top level only)."""
    for child in body.iterchildren():
        if child.tag == _W + "p":
            yield "p", _para_text(child)
        elif child.tag == _W + "tbl":
            yield "tbl", _table_rows(child)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _table_rows(tbl):
    """Cell texts row by row, straight from the XML (python-docx's ``row.cells``
    is quadratic on big tables). A cell spanning n grid columns is repeated n
    times and a vertically merged cell repeats the text above it, exactly
    like ``row.cells``."""
    rows, above = [], {}
    for tr in tbl.iterchildren(_W + "tr"):
        row, col = [], 0
        for tc in tr.iterchildren(_W + "tc"):
            tcpr = tc.find(_W + "tcPr")
            span, vmerge = 1, None
            if tcpr is not None:
                gs = tcpr.find(_W + "gridSpan")
                if gs is not None:
                    try:
                        span = max(1, int(gs.get(_W + "val")))
                    except (TypeError, ValueError):
                        span = 1
                vm = tcpr.find(_W + "vMerge")
                if vm is not None:
                    vmerge = vm.get(_W + "val") or "continue"
            if vmerge == "continue":
                text = above.get(col, "")
            else:
                paras = [_para_text(p) for p in tc.iterchildren(_W + "p")]
                text = _clean("\n".join(paras))
            for k in range(span):
                above[col + k] = text
                row.append(text)
            col += span
        rows.append(row)
    return rows


def _clean(text):
    return " ".join((text or "").replace(" ", " ").split())


SMALL = {"and", "of", "the", "for", "in", "on", "to", "a", "an", "or", "with"}
# Short capitals that really are abbreviations (kept as written). Other short
# words in an all-capitals heading ("DRY", "GAS", "OIL", "LAB") are title-cased.
ACRONYMS = {"ND", "HND", "NID", "NVC", "ICT", "IT", "GIS", "GPS", "CAD", "CAM", "CNC", "PLC", "LAN", "WAN",
            "DNA", "PCR", "ECG", "EEG", "ENT", "ICU", "OPD", "USB", "UPS", "LCD", "LED", "TV", "PC", "HIV",
            "AIDS", "UV", "IR", "AC", "DC", "NDT", "MIG", "TIG", "MMA", "CPU", "SEM", "XRD", "AAS", "HPLC",
            "GC", "NMR", "PH", "PPE", "CCTV", "MRI", "ICTS", "RF", "VHF", "UHF", "GSM", "LPG", "CNG", "HVAC"}
ROMAN = re.compile(r"^(?=[IVX]+$)X{0,3}(IX|IV|V?I{0,3})$")


def _keep_caps(word):
    core = re.sub(r"[^A-Za-z]", "", word)
    if not core or not word.isupper():
        return False
    if core in ACRONYMS or ROMAN.match(core):
        return True
    # e.g. "HND", "CNC": no vowel at all reads as an abbreviation
    return len(core) <= 4 and not re.search(r"[AEIOUY]", core)


def smart_title(text):
    """'WATER RESOURCES AND IRRIGATION' → 'Water Resources and Irrigation';
    'LAUNDRY AND DRY CLEANING' → 'Laundry and Dry Cleaning' (keeps ND, HND,
    ICT, CNC and similar abbreviations)."""
    words = []
    for i, w in enumerate(text.split()):
        lw = w.lower()
        if i and lw in SMALL:
            words.append(lw)
        elif _keep_caps(w):
            words.append(w)
        else:
            words.append("/".join("-".join(part[:1].upper() + part[1:].lower() for part in seg.split("-"))
                                  for seg in w.split("/")))
    return " ".join(words)


def heading_title(text):
    """Title-case an all-capitals heading, also when it ends in a note in
    ordinary case: 'GENERAL CHEMISTRY LABORATORY (HND only)' →
    'General Chemistry Laboratory (HND only)'."""
    m = re.match(r"^(.*?)(\s*\([^()]*\))?$", text)
    head, tail = (m.group(1), m.group(2) or "") if m else (text, "")
    if head and head.isupper():
        return smart_title(head) + tail
    return text


def levels_from_source(source):
    """'HND Nursing (…); ND Nursing (…)' → ['ND', 'HND'] (used when the file has
    no 'National Diploma …' line, e.g. a programme whose curricula hold no list)."""
    found = set()
    for part in re.split(r"[;,]", source or ""):
        m = re.match(r"\s*(HND|ND)\b", part.strip(), re.I)
        if m:
            found.add(m.group(1).upper())
    return [lv for lv in ("ND", "HND") if lv in found]


def qty_number(text):
    """'5' → 5, '1 set' → 1, '-' / '' → None, '2-3' → 3 (upper bound)."""
    t = _clean(text)
    if not t or t in ("-", "–", "—", "N/A", "n/a", "Nil", "nil"):
        return None
    nums = re.findall(r"\d+", t)
    return int(nums[-1]) if nums else None


def parse(fileobj, filename=""):
    body = _body(fileobj)
    out = {"programme": "", "levels": [], "source": "", "notes": [], "sections": [], "file": filename}
    current = None
    in_notes = False
    paragraphs_seen = 0
    for kind, block in _iter_blocks(body):
        if kind == "p":
            text = _clean(block)
            if not text:
                continue
            paragraphs_seen += 1
            up = text.upper()
            if any(up.startswith(s) for s in SKIP_TITLES):
                continue
            if not out["programme"] and paragraphs_seen <= 3:
                out["programme"] = heading_title(text)
                continue
            if up.startswith("NATIONAL DIPLOMA") or up.startswith("HIGHER NATIONAL DIPLOMA"):
                out["levels"] = [lv for lv in ("ND", "HND") if f"({lv})" in up]
                continue
            if up.startswith("SOURCE CURRICULA"):
                out["source"] = text.split(":", 1)[-1].strip()
                continue
            if up.startswith("WHERE THE ND AND HND"):
                continue
            if up.startswith("THE SOURCE CURRICULA") and not out["sections"]:
                out["notes"].append(text)      # "… do not contain a list …" (no facilities)
                continue
            if up == "NOTES":
                in_notes = True
                continue
            m = SECTION_RE.match(text)
            if m and not in_notes:
                name = m.group(2).strip()
                current = {"code": m.group(1), "name": heading_title(name), "items": []}
                out["sections"].append(current)
                continue
            if in_notes:
                out["notes"].append(text)
            elif current is not None and not current["items"]:
                # a line under the facility heading, e.g. "See Science Laboratory Technology Curriculum"
                current["note"] = (current.get("note", "") + " " + text).strip()
            continue
        # a table
        rows = block
        if not rows:
            continue
        header = [h.lower() for h in rows[0]]
        if not any("description" in h or "item" in h for h in header):
            continue
        if current is None:
            current = {"code": "A", "name": "Physical facilities", "items": []}
            out["sections"].append(current)
        name_i = next((i for i, h in enumerate(header) if "description" in h or h == "item" or "items" in h), 1)
        nd_i = next((i for i, h in enumerate(header) if h.startswith("nd")), None)
        hnd_i = next((i for i, h in enumerate(header) if h.startswith("hnd")), None)
        qty_i = next((i for i, h in enumerate(header) if "quantity" in h or h in ("qty", "no.", "no")), None)
        rem_i = next((i for i, h in enumerate(header) if "remark" in h), None)
        levels = out["levels"] or ["ND"]
        group = ""
        for r in rows[1:]:
            if len(r) <= name_i or not r[name_i]:
                continue
            if len(set(c for c in r if c)) == 1:   # a merged row: a sub-group heading inside the table
                if r[name_i].lower().startswith("no items listed"):
                    current["note"] = current.get("note") or r[name_i]
                    continue
                group = r[name_i]
                continue
            item = {"sn": r[0] if r else "", "name": r[name_i], "nd": "", "hnd": "", "group": group,
                    "remarks": r[rem_i] if rem_i is not None and rem_i < len(r) else ""}
            if nd_i is not None or hnd_i is not None:
                item["nd"] = r[nd_i] if nd_i is not None and nd_i < len(r) else ""
                item["hnd"] = r[hnd_i] if hnd_i is not None and hnd_i < len(r) else ""
            elif qty_i is not None and qty_i < len(r):
                if levels == ["HND"]:
                    item["hnd"] = r[qty_i]
                else:
                    item["nd"] = r[qty_i]
                    if "HND" in levels and "ND" not in levels:
                        item["hnd"], item["nd"] = r[qty_i], ""
            current["items"].append(item)
    if not out["levels"]:
        out["levels"] = levels_from_source(out["source"]) or ["ND"]
    return out


def rows_from(parsed):
    """Flat rows in the Excel template's column order (for the import preview)."""
    for order_s, sec in enumerate(parsed["sections"], 1):
        for order_i, it in enumerate(sec["items"], 1):
            yield {"programme": parsed["programme"], "levels": ", ".join(parsed["levels"]),
                   "facility": sec["name"], "facility_code": sec["code"], "group": it.get("group", ""),
                   "name": it["name"],
                   "nd_qty": it["nd"], "hnd_qty": it["hnd"], "remarks": it["remarks"],
                   "sort_order": order_s * 1000 + order_i}
