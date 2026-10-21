"""NBTE reference data and name normalisation for the nominal roll.

Shared by the importer (staff.nominal_roll), the seed command (`seed_nbte`),
the exporter and the tests. Pure functions + constant tables — no database
access here.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
_WS = re.compile(r"\s+")


def clean_text(value) -> str:
    """Strip and collapse internal whitespace. None -> ''."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return _WS.sub(" ", str(value)).strip()


def compact_key(value) -> str:
    """Upper-case, letters/digits only: 'Int. Aud. Dept' -> 'INTAUDDEPT'."""
    return re.sub(r"[^A-Z0-9]", "", clean_text(value).upper())


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------
# (name used on the nominal roll, preferred code, full title, aliases)
# `name` is the abbreviation HR uses in the DEPARTMT column; section-header
# titles and other spellings found in the file are aliases.
DEPARTMENTS = [
    ("ES OFFICE", "es-office", "Executive Secretary's Office",
     ["EXECUTIVE SECRETARY OFFICE", "ES OFFICE"]),
    ("ICT", "ict", "ICT Department",
     ["I CT DEPARTMENT", "ICT DEPARTMENT", "INFORMATION AND COMMUNICATION TECHNOLOGY"]),
    ("INT. AUD. DEPT", "int-aud-dept", "Internal Audit Department",
     ["INTERNAL AUDIT DEPARTMENT", "INTERNAL AUDIT"]),
    ("PRCM", "prcm", "Procurement Department",
     ["PROCURREMENT DEPARTMENT", "PROCUREMENT DEPARTMENT", "PROCUREMENT"]),
    ("COE", "coe", "Centre of Excellence", ["CENTRE OF EXCELLENCE"]),
    ("FINANCE", "finance", "Finance and Account Department",
     ["FINANCE AND ACCOUNT DEPARTMENT", "FINANCE AND ACCOUNTS"]),
    ("HUMAN RESOURCE", "hr", "Human Resources Department",
     ["HUMAN RESOURCES DEPARTMENT", "HUMAN RESOURCES", "HAMAN RESOURCES", "HUMAN RESOURCE"]),
    ("SEVICOM", "sevicom", "SERVICOM Department", ["SERVICOM DEPARTMENT", "SERVICOM"]),
    ("ASPD", "aspd", "Academic and Strategic Planning Department",
     ["ACADEMIC AND STRATEGIC PLANNING DEPARTMENT"]),
    ("PPD", "ppd", "Physical Planning & Development Department",
     ["PHYSICAL PLANNING & DEVELOPMENT DEPARTMENT", "PHYSICAL PLANNING AND DEVELOPMENT DEPARTMENT"]),
    ("GAMD", "gamd", "General Administration and Management Department",
     ["GENERAL ADMINISTRATION AND MANAGEMENT DEPARTMENT"]),
    ("IRD", "ird", "Innovation and Research Department", ["INNOVATION AND RESEARCH DEPARTMENT"]),
    ("TS&DD", "tsdd", "Technical Skills & Development Department",
     ["TECHNICAL SKILLS & DEVELOPMENT DEPARTMENT", "TECHNICAL SKILLS AND DEVELOPMENT DEPARTMENT"]),
    ("NTIRD", "ntird", "New TVET Institutions Registration Department",
     ["NEW TVET INSTITUTIONS REGISTRATION DEPARTMENT"]),
    ("DPAP", "dpap", "Polytechnic and Allied Programmes Department",
     ["POLYTECHNIC AND ALLIED PROGRAMMES DEPARTMENT"]),
    ("INPD", "inpd", "Inspectorate Programmes Department", ["INSPECTORATE PROGRAMMES DEPARTMENT"]),
    ("CURR. DEPT.", "curr-dept", "Curriculum Department", ["CURRICULUM DEPARTMENT"]),
    ("NHSPD", "nhspd", "Nursing and Health Sciences Programmes Department",
     ["NURSING AND HEALTH SCIENCES PROGRAMMES DEPARTMENT"]),
    ("ZONAL OFFICE", "zonal-office", "Zonal Offices", []),
    ("LIAISON OFFICE", "liaison-office", "NBTE Liaison Office, Abuja",
     ["NBTE LIAISON OFFICE ABUJA", "LIAISON OFFICE ABUJA"]),
]

# Other abbreviations seen in the DEPARTMT column, with the section title that
# explains them. Not seeded (they are older/other units); the importer
# creates them on demand and uses these titles to recognise section headers.
EXTRA_DEPARTMENT_ALIASES = {
    "SPD": ["STRATEGY AND PARTNERSHIP DEPARTMENT", "DIRECTOR SPECIAL PROJECT SOUTH SOUTH"],
    "ACPD": ["ACADEMIC PLANNING DEPARTMENT"],
    "VTS&D": ["VOCATIONAL TECHNICAL SKILLS & DEVELOPMENT DEPARTMENT"],
}

ZONAL_CITIES = ["AWKA", "BAUCHI", "KATSINA", "LAGOS", "NASARAWA", "PORT-HARCOURT"]


def department_alias_index():
    """compact_key(alias) -> canonical department name."""
    index = {}
    for name, _code, title, aliases in DEPARTMENTS:
        for a in [name, title, *aliases]:
            index[compact_key(a)] = name
    for name, aliases in EXTRA_DEPARTMENT_ALIASES.items():
        for a in [name, *aliases]:
            index[compact_key(a)] = name
    return index


def zonal_office_from(text) -> str:
    """'SOUTH EAST ZONAL OFFICE  AwKA ANAMBRA STATE' -> 'ZONAL OFFICE AWKA'."""
    up = clean_text(text).upper()
    if "ZONAL OFFICE" not in up and "ZONAL" not in up:
        return ""
    for city in ZONAL_CITIES:
        if city in up or city.replace("-", " ") in up:
            return f"ZONAL OFFICE {city}"
    return ""


# ---------------------------------------------------------------------------
# States of origin (36 + FCT) with geopolitical zones
# ---------------------------------------------------------------------------
STATES = [
    ("ABIA", "ab", "SE"), ("ADAMAWA", "ad", "NE"), ("AKWA IBOM", "ak", "SS"),
    ("ANAMBRA", "an", "SE"), ("BAUCHI", "ba", "NE"), ("BAYELSA", "by", "SS"),
    ("BENUE", "be", "NC"), ("BORNO", "bo", "NE"), ("CROSS RIVER", "cr", "SS"),
    ("DELTA", "de", "SS"), ("EBONYI", "eb", "SE"), ("EDO", "ed", "SS"),
    ("EKITI", "ek", "SW"), ("ENUGU", "en", "SE"), ("GOMBE", "go", "NE"),
    ("IMO", "im", "SE"), ("JIGAWA", "ji", "NW"), ("KADUNA", "kd", "NW"),
    ("KANO", "kn", "NW"), ("KATSINA", "kt", "NW"), ("KEBBI", "ke", "NW"),
    ("KOGI", "ko", "NC"), ("KWARA", "kw", "NC"), ("LAGOS", "la", "SW"),
    ("NASARAWA", "na", "NC"), ("NIGER", "ni", "NC"), ("OGUN", "og", "SW"),
    ("ONDO", "on", "SW"), ("OSUN", "os", "SW"), ("OYO", "oy", "SW"),
    ("PLATEAU", "pl", "NC"), ("RIVERS", "ri", "SS"), ("SOKOTO", "so", "NW"),
    ("TARABA", "ta", "NE"), ("YOBE", "yo", "NE"), ("ZAMFARA", "za", "NW"),
    ("FCT", "fc", "NC"),
]

STATE_ALIASES = {
    "AKIBOM": "AKWA IBOM", "AKWAIBOM": "AKWA IBOM",
    "CRIVER": "CROSS RIVER", "CROSSRIVER": "CROSS RIVER",
    "KAN0": "KANO", "KASTINA": "KATSINA",
    "ABUJA": "FCT", "FCTABUJA": "FCT", "FEDERALCAPITALTERRITORY": "FCT",
    "NASSARAWA": "NASARAWA",
}


def canonical_state(value) -> str:
    """Map a spelling from the file to one of STATES' names, or '' if unknown."""
    key = compact_key(value)
    if not key:
        return ""
    if key in STATE_ALIASES:
        return STATE_ALIASES[key]
    for name, _code, _zone in STATES:
        if compact_key(name) == key:
            return name
    return ""


# ---------------------------------------------------------------------------
# Staff states (REMARKS column)
# ---------------------------------------------------------------------------
REMARK_ALIASES = {
    "CONFIRMED": "Confirmed", "COMFIRMED": "Confirmed", "CONFIRM": "Confirmed",
    "PROBATION": "Probation", "ONPROBATION": "Probation",
}


def canonical_remark(value) -> str:
    """REMARKS -> StaffState name. Unknown values keep the file's text."""
    text = clean_text(value)
    if not text:
        return ""
    return REMARK_ALIASES.get(compact_key(text), text)


# ---------------------------------------------------------------------------
# Designations
# ---------------------------------------------------------------------------
_TOKEN_MAP = {
    "SNR": "SENIOR", "SN": "SENIOR", "SEN": "SENIOR", "SENIOR": "SENIOR",
    "PRIN": "PRINCIPAL", "PRNC": "PRINCIPAL", "PRINCIPAL": "PRINCIPAL",
    "CH": "CHIEF", "CHIEF": "CHIEF", "CHIEFDRIVER": "CHIEF DRIVER",
    "ASST": "ASSISTANT", "ASSIT": "ASSISTANT", "AST": "ASSISTANT", "ASSISTANT": "ASSISTANT",
    "PROG": "PROGRAMME", "PROGS": "PROGRAMME", "PROGRS": "PROGRAMME", "PROGRAMME": "PROGRAMME",
    "PROGRAMMES": "PROGRAMME",
    "OFF": "OFFICER", "OFFR": "OFFICER", "OFFICER": "OFFICER", "OFR": "OFFICER",
    "OFFRICER": "OFFICER",
    "PLNG": "PLANNING", "PLANG": "PLANNING", "PLANNING": "PLANNING", "PLANING": "PLANNING",
    "EXE": "EXECUTIVE", "EXEC": "EXECUTIVE", "EXECUTIVE": "EXECUTIVE", "EX": "EXECUTIVE",
    "EXECUTI": "EXECUTIVE",
    "TECH": "TECHNICAL", "TECHNICAL": "TECHNICAL", "HIGH": "HIGHER", "HIGHER": "HIGHER",
    "HIHHER": "HIGHER",
    "RES": "RESOURCE", "RESOURCE": "RESOURCE", "RESOURCES": "RESOURCE", "RESC": "RESOURCE",
    "ACCT": "ACCOUNTANT", "ACCOUNTANT": "ACCOUNTANT", "ACCOUTANT": "ACCOUNTANT",
    "PROC": "PROCUREMENT", "PROCUREMENT": "PROCUREMENT",
    "CONF": "CONFIDENTIAL", "SEC": "SECRETARY", "STAT": "STATISTICS",
    "MED": "MEDICAL", "LAB": "LABORATORY", "INT": "INTERNAL", "NUR": "NURSING",
    "MECH": "MECHANIC", "DIERCTOR": "DIRECTOR", "AGT": "ACTING", "AG": "ACTING",
    "ACTING": "ACTING", "PROSC": "PROCESSING", "PRO": "PROCESSING", "PROCESS": "PROCESSING",
    "CL": "CLERICAL", "PHAM": "PHARMACY", "DENTAL": "DENTAL", "STORE": "STORES",
    "STORES": "STORES", "PERS": "PERSONNEL", "ACCTS": "ACCOUNTS", "ARC": "ARCHITECTURE",
    "LEGAL": "LEGAL", "ELECT": "ELECTRICAL", "PLUMB": "PLUMBING", "CARPT": "CARPENTRY",
    "CONSUL": "CONSULTANT",
}
_ROMAN = {"I", "II", "III"}


def designation_key(value) -> str:
    """Normalise a DESIGNATION cell so spellings of one post compare equal.

    'SNR. PROG. OFFR.', 'SNR.  PROG. OFFR', 'Snr. Prog. Officer' ->
    'SENIOR PROGRAMME OFFICER'. Returns '' for empty or purely numeric values.
    """
    text = clean_text(value).upper()
    if not text or re.fullmatch(r"[\d.\s]+", text):
        return ""
    if re.fullmatch(r"CON[A-Z]*\.?\s*\d+", text):   # a grade ('CONHES 06'), not a post
        return ""
    compact = re.sub(r"[^A-Z]", "", text)
    # H.T.O / HTO / H. T. O / H.TO -> Higher Technical Officer; HEO likewise.
    if compact.startswith("HTO") and len(compact) <= 9:
        rest = compact[3:]
        return ("HIGHER TECHNICAL OFFICER " + rest).strip()
    if compact == "HEO":
        return "HIGHER EXECUTIVE OFFICER"
    tokens = [t for t in re.split(r"[^A-Z0-9]+", text) if t]
    out = []
    for t in tokens:
        mapped = _TOKEN_MAP.get(t, t)
        out.extend(mapped.split())
    # Drop duplicated adjacent tokens ('ASST. CH. . EXEC.' noise).
    dedup = []
    for t in out:
        if not dedup or dedup[-1] != t:
            dedup.append(t)
    # 'HUMAN RES. II. OFF.' -> grade numeral at the end, like 'HUMAN RES. OFFR. II'.
    numerals = [t for t in dedup[:-1] if t in _ROMAN]
    if len(numerals) == 1 and dedup[-1] not in _ROMAN:
        dedup.remove(numerals[0])
        dedup.append(numerals[0])
    return " ".join(dedup)


_TRADE_WORDS = {"DRIVER", "MECHANIC", "FOREMAN", "ARTISAN", "FIREMAN"}


def designation_rank(value) -> int:
    """The seed heuristic (see SETUP.md). Higher = more senior."""
    key = designation_key(value)
    if not key:
        return 0
    words = set(key.split())
    roman = key.split()[-1] if key.split()[-1] in _ROMAN else ""
    plus_one = 1 if roman == "I" else 0
    if "EXECUTIVE SECRETARY" in key:
        return 100
    if "DIRECTOR" in words and "DEPUTY" not in words and "ASSISTANT" not in words:
        return 90
    if "DEPUTY" in words and "DIRECTOR" in words:
        return 80
    if words & _TRADE_WORDS:
        if "CHIEF" in words:
            return 20
        if "SENIOR" in words or "HEAVY" in words:
            return 15
        return 10
    if "ASSISTANT" in words and ("CHIEF" in words or "DIRECTOR" in words):
        return 65
    if "CHIEF" in words or key.startswith("HEAD OF") or "MATRON" in words and "CHIEF" in words:
        return 70
    if "CONSULTANT" in words or "PRINCIPAL" in words:
        return 60 + plus_one
    if "SENIOR" in words:
        return 57 + plus_one
    if "HIGHER" in words:
        return 55 + plus_one
    if roman == "I":
        return 40
    if roman == "II":
        return 35
    if roman == "III":
        return 30
    if "EXECUTIVE" in words and "OFFICER" in words and "ASSISTANT" not in words:
        return 25
    if "CLERICAL" in words:
        return 20
    if "ASSISTANT" in words:
        return 15
    if "OFFICER" in words:      # a bare 'X OFFICER' with no grade numeral: entry grade
        return 30
    return 0


JUNIOR_MAX_LEVEL = 6


def junior_rank(title_rank: int) -> int:
    """Squeeze the title rank of a junior-cadre post (CONTEDISS 6 and below,
    e.g. CHIEF CLERICAL OFF.) into the 10-24 band so it never outranks a
    senior-cadre officer. Order within the cadre is kept."""
    if title_rank <= 25:
        return title_rank
    return min(24, 10 + title_rank // 5)


_SENIORITY_WORDS = {"SENIOR", "CHIEF", "PRINCIPAL", "ASSISTANT", "HIGHER", "ACTING", "DEPUTY"}


def designation_family(value) -> str:
    """Strip seniority words and the trailing grade numeral:
    'ASSISTANT CHIEF PROGRAMME OFFICER' and 'PROGRAMME OFFICER II' ->
    'PROGRAMME OFFICER'."""
    words = designation_key(value).split()
    if words and words[-1] in _ROMAN:
        words = words[:-1]
    return " ".join(w for w in words if w not in _SENIORITY_WORDS)
