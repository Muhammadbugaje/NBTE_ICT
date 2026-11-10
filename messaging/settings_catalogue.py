"""v2.1 Phase E (M5, M8) memo settings, seeded by `seed_nbte` (set `settings`).

Columns: key, value, type, group, description, editable_by, dual.
The letterhead text follows the owner's NBTE external letterhead; the ES's
name is taken from whoever holds the Executive Secretary post.
"""

G_MEMOS = "Memos"
G_CHAT = "Messages"
EDIT = "config.change_configentry"

SETTINGS = [
    ("memo.external.organisation", "NATIONAL BOARD FOR TECHNICAL EDUCATION", "str", G_MEMOS,
     "Name at the top of the external letterhead (memos to institutions).", EDIT, False),
    ("memo.external.address", "Plot B, Bida Road, P.M.B. 2239, Kaduna, Nigeria.", "str", G_MEMOS,
     "Address printed on the right of the external letterhead.", EDIT, False),
    ("memo.external.email", "es@nbte.gov.ng", "str", G_MEMOS,
     "E-mail printed on the external letterhead.", EDIT, False),
    ("memo.external.phone", "", "str", G_MEMOS,
     "Telephone printed on the external letterhead (blank = not printed).", EDIT, False),
    ("memo.external.es_postnominals", "", "str", G_MEMOS,
     "Honours printed after the Executive Secretary's name on the letterhead, e.g. 'FNSChE, C.Engr. (COREN)'.",
     EDIT, False),
    ("memo.external.reference_prefix", "NBTE/ES", "str", G_MEMOS,
     "Prefix of the reference on external letters (followed by the memo reference).", EDIT, False),
    ("memo.institution_new_days", "365", "int", G_MEMOS,
     "An institution counts as 'new' in the memo filter when it was approved (or registered) "
     "within this many days.", EDIT, False),
    ("messaging.pins_per_chat", "3", "int", G_CHAT,
     "How many messages can be pinned inside one chat (shown in the bar under the chat header).", EDIT, False),
    ("messaging.invite_link_days", "7", "int", G_CHAT,
     "How many days a new group invite link works unless the admin chooses otherwise (1-30).", EDIT, False),
    ("messaging.poll_cache_seconds", "5", "int", G_CHAT,
     "Seconds the server reuses an unchanged inbox or notification-count answer for the live polls (0 = off).",
     EDIT, False),
]
