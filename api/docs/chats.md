# Chats API (staff door)

Base: `api/v1/chats/` on the **staff door** only. Every endpoint needs
`Authorization: Bearer <access>`. Portal accounts get `403 wrong_door`; on the
portal host these routes do not exist (`404 not_found`).

All behaviour is the website's own (`messaging/chat_services.py`,
`messaging/voice.py`): the same participant scoping, 15-minute edit window,
5 MB attachment limit, receipts, reactions, polls and voice-note pipeline.

## Conventions

* Date-times are ISO 8601 with offset (`2026-10-22T09:12:00+01:00`).
* A conversation you are not in (or deleted for yourself), and a message that
  is not visible to you (deleted "for me", or in such a conversation), answer
  **404 `not_found`** - never 403.
* A rule the website enforces (empty message, edit window passed, pin limit,
  wrong emoji, poll in a direct chat …) answers **400 `validation_error`**
  with the website's message in `error.message` (and in
  `error.fields.non_field_errors`). Request-shape errors list the field in
  `error.fields`:

```json
{"error": {"code": "validation_error", "message": "That reaction is not available.",
           "fields": {"non_field_errors": ["That reaction is not available."]}}}
```

```json
{"error": {"code": "validation_error", "message": "Please check the highlighted fields.",
           "fields": {"duration": ["\"2y\" is not a valid choice."]}}}
```

* Common errors on every endpoint: 401 `not_authenticated`/`token_expired`,
  403 `wrong_door`/`password_change_required`/`account_inactive`, 429 `throttled`.
* Lists use the standard pagination `{"results","next","previous","count"}`
  (`?page=`, `?page_size=` ≤ 100) except the message pages (cursor style, below).
* Avatar colours are slots `c0`…`c7` (the website's palette); `avatar.seed`
  is a stable integer if the app prefers its own palette.
* File downloads (`download_path`, `audio_path`) are API paths: request them
  with the same Bearer header. Never cache them outside the app sandbox.

## Polling model

* Chat list: re-fetch `GET threads/` when the list screen is visible (e.g. every
  10-15 s, and on resume). `me/` → `unread.chats` is the badge total.
* Open chat: `GET threads/<id>/messages/` once, then poll
  `GET threads/<id>/updates/?after=<last_id>&since=<server_time>` **every 3 s**,
  always passing the `last_id` and `server_time` from the previous answer.
  Append `new`, replace messages in `changed` by id, update ticks from
  `receipts`, show `typing`. Pass `visible=false` while the app is in the
  background (marks delivered, not read).
* While the user types, `POST threads/<id>/typing/` at most every 3 s; send
  `{"state": "stop"}` when the field is cleared. The indicator expires on the
  server after 6 s.

---

## The message object

Every endpoint that returns a message uses this shape (from
`chat_services.message_payload`, with API paths):

```json
{
  "id": 35,
  "thread_id": 6,
  "kind": "text",
  "sender": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II", "color": "c1"},
  "is_mine": false,
  "is_system": false,
  "body": "With a file",
  "sent_at": "2026-10-22T00:19:18.712981+01:00",
  "edited": false,
  "edited_at": null,
  "changed_at": "2026-10-22T00:19:19.268536+01:00",
  "deleted": false,
  "forwarded": false,
  "reply_to": {"id": 34, "sender_name": "Ibrahim ICT Officer (Demo)", "preview": "Hello from the API smoke test"},
  "attachments": [
    {"id": 3, "name": "plan.txt", "size_bytes": 8, "content_type": "text/plain",
     "download_path": "/api/v1/chats/attachments/3/", "expires_at": "2026-11-21T00:19:18.725344+01:00"}
  ],
  "voice_note": null,
  "poll": null,
  "reactions": [{"emoji": "👍", "count": 1, "users": ["You"], "mine": true}],
  "my_reaction": "👍",
  "starred": false,
  "receipt": null,
  "linked_task": null,
  "linked_memo": null,
  "can_edit": false,
  "can_delete_for_everyone": false
}
```

| Field | Notes |
| --- | --- |
| `kind` | `text` · `voice` · `poll` · `system` (automatic notice, `sender` may be null) · `deleted` |
| `deleted` | deleted for everyone: `body` = `"This message was deleted."`, no attachments/voice/poll/reactions |
| `edited` | true once edited (the bubble shows "edited") |
| `changed_at` | bumped on edit/delete/reaction/vote/transcript/link - drives `updates.changed` |
| `reply_to` | quoted message preview; `preview` is `"🎤 Voice note (0:42)"` for voice, `"Message unavailable"` (sender `"Unavailable"`) if the quoted message was deleted |
| `attachments[]` | download with `GET download_path`; they expire after 30 days (then 404) |
| `reactions[]` | grouped by emoji, sorted by count; `users` are names, the viewer is `"You"` |
| `receipt` | only on **your own** messages, else null: `{"state": "sent"\|"delivered"\|"read", "read_by", "delivered_to", "total", "label"}` - `label` is the website text (`"Read"`, `"Delivered"`, `"Sent · read by 1 of 2"` in groups) |
| `linked_task` / `linked_memo` | `{"id", "reference", "route": "/tasks/418"}` once a task/memo was made from the message |
| `can_edit`, `can_delete_for_everyone` | your message, within 15 min of sending and 5 min of the first read |

Voice note (`kind: "voice"`, `body` is empty):

```json
"voice_note": {
  "id": 1, "duration_ms": 3072,
  "waveform": [70, 91, 95, 96, 98, 97, 97, 98, 96, 97, 97, 98, 98, 97, 98, 99, 98, 97, 97, 97, 98, 97, 96, 97,
               99, 97, 97, 98, 97, 99, 98, 97, 98, 96, 98, 97, 97, 96, 98, 97, 96, 98, 97, 96, 100, 99, 98, 39],
  "size_bytes": 10464, "mime": "audio/mp4",
  "audio_path": "/api/v1/chats/voice/1/audio/",
  "played": true,
  "transcript_status": "done",
  "transcript": "Dummy transcript of a 3 second voice note.",
  "transcript_language": "en",
  "transcript_edited": false
}
```

`waveform` is always 48 bars (0-100). `played` is false until you play it
(always true for the sender) - show the unplayed dot. `transcript_status`:
`pending` (show "Transcribing…" and wait for the message to come back in
`updates.changed`), `done`, `failed`, `disabled` (sender turned transcription
off), `unavailable` (no transcription engine on the server).

Poll (`kind: "poll"`, `body` = the question):

```json
"poll": {
  "id": 1, "question": "Meeting day?", "allow_multiple": false, "closed": false, "closes_at": null,
  "voter_count": 1, "voted": true,
  "options": [
    {"id": 1, "text": "Monday", "votes": 1, "percent": 100, "mine": true, "leading": true,
     "voters": ["Hauwa Director of ICT (Demo)"]},
    {"id": 2, "text": "Tuesday", "votes": 0, "percent": 0, "mine": false, "leading": false, "voters": []}
  ]
}
```

---

## Threads

### GET `threads/` — chat list

Query: `filter` = `all` (default) · `unread` · `groups` · `direct` · `archived`;
`q` (matches the title or a participant's name); `page`, `page_size`.
Pinned chats come first, then newest activity.

```json
{
  "results": [
    {
      "id": 6,
      "title": "Smoke test chat",
      "kind": "group",
      "avatar": {"initials": "STC", "color": "c6", "seed": 6},
      "participants": [
        {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II", "color": "c1"},
        {"id": 10, "name": "Amina Officer (Demo)", "initials": "AO", "color": "c2"}
      ],
      "participant_count": 3,
      "last_message": {"id": 34, "kind": "text", "preview": "Hello from the API smoke test",
                       "sender_name": "Ibrahim ICT Officer (Demo)", "sent_at": "2026-10-22T00:19:18.337883+01:00"},
      "last_message_at": "2026-10-22T00:19:18.337883+01:00",
      "unread": 1,
      "pinned": false,
      "muted": false,
      "muted_until": null,
      "archived": false,
      "typing": []
    }
  ],
  "next": null, "previous": null, "count": 1
}
```

* `title`: the subject, or for chats without one the other people's names
  (`"Musa Ali"`, `"A, B, C +2"`).
* `participants`: up to 5 *other* people. `participant_count` includes you.
* `last_message.kind`: `text` · `voice` (`"Voice note 0:42"`) · `poll`
  (`"Poll: Meeting day?"`) · `attachment` (`"Attachment"` / `"3 attachments"`)
  · `system` · `deleted` (`"This message was deleted."`). The app adds its icon.
  `sender_name` is `"You"` for your own message. `last_message` is null for an
  empty chat.
* `unread`: exactly the website's count; the sum over the list equals the
  website inbox total and `me/` → `unread.chats` (archived chats excluded from
  `all`).
* `typing`: first names of people typing right now.
* Errors: 400 (`filter` not one of the values).

### POST `threads/` — new conversation

JSON or multipart (for `attachments`). Same rules as the website's "New message":
recipients must be in `contacts/`; `body` is required.

```json
{"participants": [16, 10], "subject": "Smoke test chat", "body": "Hello from the API smoke test"}
```

Multipart: `participants` repeated, `subject`, `body`, `attachments` repeated (≤ 5 MB in total).
Two people (you + one) make a `direct` chat, more make a `group`. A new chat is
created every time (as on the website).

**201** → the thread detail (below). Errors: 400 `fields.participants` /
`fields.body`; 400 `"No valid recipients."` (nobody you can message);
400 `"Attachments must total no more than 5 MB."`.

### GET `contacts/?q=` — people I can message

The website's compose picker: active staff (with a staff record), not you.
`q` matches name, e-mail or department (all words must match). Paginated.

```json
{"results": [{"id": 16, "name": "Hauwa Director of ICT (Demo)", "initials": "HD", "color": "c0",
              "email": "qa.ict_director@demo.nbte.local", "department": "ICT", "designation": "DIRECTOR"}],
 "next": null, "previous": null, "count": 1}
```

### GET `threads/<id>/` — chat info

```json
{
  "id": 6,
  "title": "Smoke test chat",
  "subject": "Smoke test chat",
  "kind": "group",
  "avatar": {"initials": "STC", "color": "c6", "seed": 6},
  "created_by": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II", "color": "c1"},
  "created_at": "2026-10-22T00:19:18.327754+01:00",
  "participants": [
    {"id": 16, "name": "Hauwa Director of ICT (Demo)", "initials": "HD", "color": "c0", "role": "member",
     "is_me": true, "designation": "DIRECTOR", "department": "ICT", "joined_at": "2026-10-22T00:19:18.332276+01:00"},
    {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II", "color": "c1", "role": "creator",
     "is_me": false, "designation": null, "department": "ICT", "joined_at": "2026-10-22T00:19:18.337201+01:00"}
  ],
  "pinned": false,
  "muted": false,
  "muted_until": null,
  "archived": false,
  "unread": 1,
  "can_post": true,
  "can_create_poll": true,
  "reaction_emojis": ["👍", "❤️", "😂", "😮", "😢", "🙏", "👏", "✅"],
  "limits": {"attachments_total_bytes": 5242880, "voice_max_seconds": 900, "poll_options_min": 2,
             "poll_options_max": 6, "edit_window_minutes": 15}
}
```

`role` is `creator` or `member` (chats have no admins). `can_create_poll` is
true for group chats only. Use `reaction_emojis` for the reaction bar (only
these are accepted). Does **not** mark anything read. Errors: 404.

### DELETE `threads/<id>/` — delete chat for me

The website rule: the chat disappears for you (all its messages so far are
hidden for you); if someone writes again it comes back with only the new
messages. When every participant has deleted it, it is erased with its files.
**204**. Errors: 404.

---

## Messages

### GET `threads/<id>/messages/`

Query (use at most one of `before`, `after`, `around`):

| Param | Meaning |
| --- | --- |
| *(none)* | the newest page |
| `before=<message id>` | older page (scrolling up) |
| `after=<message id>` | newer page (after a search jump) |
| `around=<message id>` | the page centred on that message (search result); 404 if you cannot see it |
| `limit` | 1-100, default 30 |

Within a page messages are **chronological** (oldest first). Opening a page
marks the chat **read and delivered** for you (like opening it on the website).

```json
{
  "results": [ { …message… }, { …message… } ],
  "next": "http://host/api/v1/chats/threads/6/messages/?before=34&limit=30",
  "previous": null,
  "count": null,
  "has_older": true,
  "has_newer": false,
  "server_time": "2026-10-22T00:19:18.925103+01:00",
  "last_id": 35
}
```

`next` = older page URL (null when `has_older` is false), `previous` = newer
page URL. Start polling `updates/` with `after=last_id` (the newest message id
in the chat) and `since=server_time`. Errors: 400 (two cursors, `limit` > 100), 404.

### POST `threads/<id>/messages/` — send

JSON `{"body": "Hi", "reply_to": 34}` or multipart with `body`, `reply_to`,
`attachments` (repeated; ≤ 5 MB in total). Body may be empty only if there
are attachments.

**201** → message. Errors: 400 `"Write a message or attach a file."`,
`"The message you're replying to is no longer available."`,
`"Attachments must total no more than 5 MB."`; 404.

### GET `messages/<id>/` — one message (fresh copy)
**200** → message. 404 if hidden for you.

### PATCH `messages/<id>/` — edit
`{"body": "With a file (edited)"}` → **200** message (`edited: true`).
400 `"This message can no longer be edited for everyone."` (not yours, or the
window passed), 400 `fields.body` (empty).

### DELETE `messages/<id>/?scope=me|everyone`
(`scope` may also be sent in a JSON body.)
* `me` → **204**; the message is hidden for you only.
* `everyone` → **200** message with `kind: "deleted"`; only the sender within
  the edit window (else 400 `"This message can no longer be deleted for everyone."`).
  Files are erased.
400 `fields.scope` if missing/wrong.

### GET `threads/<id>/updates/?after=&since=&visible=` — live updates

| Param | Meaning |
| --- | --- |
| `after` | the `last_id` you already have (0 = everything, max 100 per answer) |
| `since` | the previous answer's `server_time` (for edits/reactions/votes/transcripts) |
| `visible` | `true` (default) marks read; `false` only marks delivered |

```json
{
  "server_time": "2026-10-22T00:19:19.084584+01:00",
  "last_id": 35,
  "new": [ { …message… } ],
  "changed": [ { …message… } ],
  "receipts": {
    "35": {"state": "sent", "read_by": 1, "delivered_to": 1, "total": 2, "label": "Sent · read by 1 of 2"},
    "34": {"state": "read", "read_by": 2, "delivered_to": 2, "total": 2, "label": "Read by 2 of 2"}
  },
  "typing": [{"id": 10, "name": "Amina Officer (Demo)", "first_name": "Amina"}]
}
```

`receipts` covers your latest 100 own messages (keyed by id as a string) - use
it to move the ticks. Header `Cache-Control: no-store`. Errors: 400, 404.

### POST `threads/<id>/typing/`
`{}` or `{"state": "typing"}` / `{"state": "stop"}` → **204**.

### POST `threads/<id>/read/`
Marks everything read → `{"marked": 1, "unread": 0}` (`marked` = messages newly marked).

### POST `threads/<id>/pin/`
`{"pinned": true}` → `{"pinned": true}`. At most 5 pinned chats: 400
`"You can pin up to 5 chats. Unpin one first."`.

### POST `threads/<id>/mute/`
`{"duration": "8h" | "1w" | "always" | "off"}` →
`{"muted": true, "muted_until": "2026-10-22T08:19:21.810023+01:00"}`
(`always` = year 2999; `off` → `{"muted": false, "muted_until": null}`).

### POST `threads/<id>/archive/` · POST `threads/<id>/unarchive/`
→ `{"archived": true}` / `{"archived": false}`. Archived chats appear only
under `filter=archived` (and do not count in the unread badge).

---

## Message actions

### POST `messages/<id>/react/`
`{"emoji": "👍"}` → **200**
`{"my_reaction": "👍", "message": { …message… }}`.
The same emoji again removes it (`my_reaction: null`); another emoji replaces
it. Only `reaction_emojis`; not on deleted/system messages (400).

### POST `messages/<id>/star/`
Toggles your star → `{"starred": true, "message": { … }}`.

### GET `starred/`
Your starred messages (newest star first), paginated:

```json
{"results": [{"starred_at": "2026-10-22T00:19:19.4+01:00", "thread_id": 6, "thread_title": "Smoke test chat",
              "message": { …message… }}],
 "next": null, "previous": null, "count": 1}
```

### POST `messages/<id>/forward/`
`{"thread_ids": [6, 9]}` (your own chats only) → **201**
`{"count": 2, "message_ids": [38, 39]}`. Text, attachments and voice notes are
copied (`forwarded: true`). 400: `"You can only forward to your own conversations."`,
`"Polls can't be forwarded."`, `fields.thread_ids` (empty).

### GET `messages/<id>/info/`
Per-person delivery for your message (any message you can see works):

```json
{
  "message_id": 35,
  "sent_at": "2026-10-22T00:19:18.712981+01:00",
  "receipt": {"state": "sent", "read_by": 1, "delivered_to": 1, "total": 2},
  "read": [{"user_id": 16, "name": "Hauwa Director of ICT (Demo)", "initials": "HD", "color": "c0",
            "read_at": "2026-10-22T00:19:18.925103+01:00", "delivered": true,
            "delivered_at": "2026-10-22T00:19:18.925103+01:00"}],
  "delivered": [],
  "pending": [{"user_id": 10, "name": "Amina Officer (Demo)", "initials": "AO", "color": "c2",
               "read_at": null, "delivered": false, "delivered_at": null}]
}
```

### POST `messages/<id>/task/` · POST `messages/<id>/memo/` — "Turn into…"

Return the prefill for the app's own create form; nothing is created:

```json
{"title": "With a file (edited)",
 "description": "With a file (edited)\n\n— From a chat message by Ibrahim ICT Officer (Demo) on 22 Oct 2026 00:19.",
 "from_message": 35}
```

For a voice note the description uses the transcript. Send `from_message`
with the create call (`POST memos/` supports it now; the task create endpoint
will accept it too) so the message shows `linked_task` / `linked_memo`.

### GET `search/?q=&limit=`
Across all your chats (text, voice-note transcripts, poll questions), newest
first. `q` ≥ 2 characters, `limit` 1-100 (default 50).

```json
{"results": [{"thread_id": 6, "thread_title": "Smoke test chat", "message_id": 37, "kind": "voice",
              "snippet": "Corrected transcript", "sender_name": "Ibrahim ICT Officer (Demo)",
              "sent_at": "2026-10-22T00:19:20.416388+01:00"}],
 "count": 1}
```

Open a hit with `GET threads/<thread_id>/messages/?around=<message_id>`.

---

## Polls

### POST `threads/<id>/polls/` (group chats only)
`{"question": "Meeting day?", "options": ["Monday", "Tuesday"], "allow_multiple": false}`
→ **201** message (`kind: "poll"`). Blank and duplicate options are dropped;
2-6 options remain or 400 `"A poll needs between 2 and 6 different options."`.
Direct chat: 400 `"Polls are available in group chats."`.

### POST `polls/<poll id>/vote/`
`{"option_ids": [1]}` → **200** message with fresh results.
Single-choice: one id replaces your vote; sending your current answer again
withdraws it. Multiple-choice: the ids are exactly your answers (`[]` withdraws).
400: `"This poll allows one answer."`, `"Choose a valid option."`, `"This poll is closed."`.
404 if you are not in the chat.

---

## Voice notes

### POST `threads/<id>/voice/` (multipart)

| Field | |
| --- | --- |
| `audio` | the recording: AAC `.m4a` (iOS/Android default) or Opus (`.ogg`, `.opus`, `.webm`); also mp3/wav/3gp. ≤ 20 MB |
| `duration_ms` | optional client duration (the server measures the real one) |
| `reply_to` | optional message id |

The server normalises to AAC-LC mono 16 kHz `.m4a`, computes the waveform and
duration, then queues transcription. **201** → message (`kind: "voice"`,
usually `transcript_status: "pending"`; the transcript arrives later through
`updates.changed`). 400: `"That file is not a supported audio recording."`,
`"That recording is too short."` (< 0.3 s), `"Voice notes can be at most 15 minutes long."`,
`"Voice notes can be at most 20 MB."`, `fields.audio` (missing).

### GET `voice/<id>/audio/`
The `.m4a` bytes (`audio/mp4`). Supports `Range: bytes=0-99` → **206** with
`Content-Range`; out-of-range → 416. `Accept-Ranges: bytes`. 404 if you are not
in the chat or the note was deleted.

### POST `voice/<id>/played/`
First play by a listener (clears the unplayed dot) → **204**.

### PATCH `voice/<id>/transcript/`
Sender only: `{"text": "Corrected transcript"}` → **200** message
(`transcript_edited: true`, `transcript_status: "done"`). Others: 403 `permission_denied`.

---

## Groups, invite links, pins and reminders (v2.1 Phase E)

Rules live in `messaging/group_services.py` (the website uses the same functions).
Members have a `role`: `owner`, `admin` or `member`. Admin-only actions answer
**403 permission_denied** for members; rule violations **400**; non-participants **404**.
Groups made by the system for a subject (`auto_managed: true`, e.g. an SSQ team)
refuse manual add/remove/leave/invite (400); archived groups (`archived_at`) are read-only.

### GET `threads/<id>/group/` — group info
Title, description, `photo_path`, the three policies (`send_policy`, `edit_info_policy`,
`add_members_policy`: `all` | `admins`), `auto_managed`, `purpose`, `archived_at`,
`member_count`, `admin_count`, `my_role`, `permissions` (flags such as `can_send`,
`can_pin`, `can_add_members`, `can_manage_members`, `read_only`), `members` (with `role`)
and `pinned` messages. Direct chats answer with `is_group: false`.

### PATCH `threads/<id>/group/` (JSON or multipart)
`title`, `description`, `photo`, `remove_photo` need edit-info permission; the policies
need an admin. Each change posts a system message. Answers the group info.

### POST `threads/<id>/members/` `{"users": [ids]}` · POST `threads/<id>/members/<user>/remove/` · POST `threads/<id>/members/<user>/role/` `{"role": "admin"|"member"}` · POST `threads/<id>/leave/`
Add (staff only, per `add_members_policy`), remove, promote/demote (admins; never the
owner), leave (204; an owner who leaves hands over to the longest-serving admin).

### GET / POST `threads/<id>/invites/` · POST `threads/<id>/invites/<invite>/revoke/`
Join links for staff: `days` (1-30, default `messaging.invite_link_days` = 7) and an
optional `max_uses`. `url` is the website join page.

### GET / POST `join/<token>/`
GET: `state` = `ok` | `member` | `expired` | `not_staff` | `closed`. POST joins (400 when
expired/revoked/used up, 403 for non-staff accounts).

### POST `messages/<id>/pin/` `{"pinned": true|false}`
Pin inside the chat: group admins (both people in a direct chat); at most
`messaging.pins_per_chat` (3). The message object carries `pinned_at`.

### POST / DELETE `messages/<id>/reminder/` · GET `reminders/`
`{"remind_at": ISO}` or `{"preset": "1h"|"3h"|"tomorrow"|"1w"}` plus an optional `note`.
One pending reminder per person per message; a notification (category `message`)
arrives at the time (beat task every minute). The message object carries `my_reminder_at`.

### Cheaper polls
`threads/<id>/updates/` accepts `v` (the `v` of your last answer). While nothing changed,
the answer is `{"unchanged": true, ...}` with empty lists and no database work; keep
your state. The website shares one notification/inbox poll between all tabs of a browser.

## Attachments

### GET `attachments/<id>/`
The file with its original name in `Content-Disposition`. `?inline=1` asks for
inline display (PDF, images, text). 404 when you are not in the chat, the
message is deleted (for everyone or for you) or the file expired (30 days).

---

## Endpoint summary

| Method | Path | Notes |
| --- | --- | --- |
| GET | `chats/threads/` | list (filters, q, paginated) |
| POST | `chats/threads/` | new conversation (201) |
| GET | `chats/contacts/` | people I can message |
| GET / DELETE | `chats/threads/<id>/` | info / delete for me (204) |
| GET / POST | `chats/threads/<id>/messages/` | page / send (201) |
| GET | `chats/threads/<id>/updates/` | 3-second poll |
| POST | `chats/threads/<id>/typing/` | 204 |
| POST | `chats/threads/<id>/read/` | |
| POST | `chats/threads/<id>/pin/` · `mute/` · `archive/` · `unarchive/` | |
| POST | `chats/threads/<id>/polls/` | 201 |
| POST | `chats/threads/<id>/voice/` | multipart, 201 |
| GET / PATCH / DELETE | `chats/messages/<id>/` | fetch / edit / delete |
| POST | `chats/messages/<id>/react/` · `star/` · `forward/` (201) · `task/` · `memo/` | |
| GET | `chats/messages/<id>/info/` | |
| GET | `chats/starred/` · `chats/search/` | |
| POST | `chats/polls/<id>/vote/` | |
| GET | `chats/voice/<id>/audio/` | Range |
| POST | `chats/voice/<id>/played/` | 204 |
| PATCH | `chats/voice/<id>/transcript/` | sender only |
| GET | `chats/attachments/<id>/` | file |
| GET / PATCH | `chats/threads/<id>/group/` | group info / settings (v2.1) |
| POST | `chats/threads/<id>/members/` · `members/<user>/remove/` · `members/<user>/role/` · `leave/` | admins (v2.1) |
| GET / POST | `chats/threads/<id>/invites/` · POST `invites/<id>/revoke/` | staff join links (v2.1) |
| GET / POST | `chats/join/<token>/` | join by link (v2.1) |
| POST | `chats/messages/<id>/pin/` | in-chat pin, max 3 (v2.1) |
| POST / DELETE | `chats/messages/<id>/reminder/` · GET `chats/reminders/` | reminders (v2.1) |
