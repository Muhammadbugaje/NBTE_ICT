# Memo routing

## 18 October 2026 — directorate routing (start here)

### The rules now in force

| Sender | Writing to | Path |
|---|---|---|
| **Director** | anyone in their own department (one person, a list, or the whole department) | **Delivered directly** |
| **Director** | another director | **Delivered directly** |
| **Director** | staff in another directorate (one person, a list, or that department as a circular) | **That directorate's director** approves, then it is delivered |
| Anyone else | a colleague in their own department | Line head (if any), then delivered |
| Anyone else | their own director | Line head → deputy director, then delivered to the director |
| Anyone else | another directorate's director | Line head → deputy → **own director**, then delivered |
| Anyone else | staff in another directorate | Line head → deputy → **own director** → **that directorate's director**, then delivered |
| Executive Secretary | anyone | Delivered directly |

**A step is skipped (and the reason is written on the memo) when:**
- nobody holds it (line head, deputy director);
- the sender is already at that level or above. A line head skips the line-head step, a deputy skips the deputy step, and a director skips every "own department" step, **even if someone else is recorded as that department's director or head**;
- the approver would be the sender;
- the approver is the recipient. For example, staff → own director: the director is not asked to "approve" a memo addressed to them; it is simply delivered.

**Directors' steps are required.** If a department has no director (no department head, no one with the Director role or a DIRECTOR designation), the memo waits. The banner says exactly what to fix, the admins are alerted, and the memo moves on by itself as soon as a director is set.

A department's director is found the same way everywhere: the department head, then the Director role, then a DIRECTOR / Acting Director designation.

### What was wrong

Checked on a copy of your database (Finance and ICT accounts), comparing your 18 Oct code with the fixed code:

| Case | Before | After |
|---|---|---|
| ICT director → Finance staff | Delivered directly (skipped the Finance director) | Finance director approves, then delivered |
| ICT officer → ICT director | Deputy, then **the director had to approve a memo addressed to himself**. It sat in his approval queue, not his inbox (the "stuck" memo); the line head was never asked | Line head → deputy → delivered to the director |
| ICT officer → Finance director | Deputy → ICT director → delivered (no line head) | Line head → deputy → ICT director → delivered |
| ICT officer → Finance staff | Line head only, then delivered | Line head → deputy → ICT director → Finance director → delivered |
| A director whose department has a second director-rank person (department head on record, a second Director account, an Acting Director designation) | The memo waited on that other person ("keep same rank" routes), i.e. stuck | Skipped: "The sender is already at director level or above" |

The engine could not express "the recipient's department's director", and its rules could not tell a memo that stays inside a department from one that leaves it.

### What changed

- **Engine** (`messaging/routing.py`):
  - a new step kind, **"Recipient's department director"** (`receiving_director`);
  - rules can now require **Department: same / other** (`MemoRouteRule.department_relation`);
  - the two skip rules above (sender already at that level; approver is the recipient). They apply only to the sender-side steps (line head, deputy, director). ES gates and specific-person steps behave exactly as before.
- **Migration:** `messaging/0008_memo_receiving_director_and_relation` adds one column (`department_relation`, default "any", so existing rules are unchanged) and one step-kind choice.
- **Screens:** the rule editor has a **Department** field; rule lists show "same department" / "other department".
- **The policy itself is data**, installed by a command. You can still edit the routes and rules in *Messages → Manage*.

### Commands

```bash
python manage.py migrate                          # messaging 0008
python manage.py seed_memo_routes                 # install the rules above (safe to re-run)
python manage.py seed_memo_routes --show          # list every active rule, highest priority first
python manage.py seed_memo_routes --dry-run       # what it would change
python manage.py seed_memo_routes --remove        # take the directorate rules out again

python manage.py seed_memo_scenarios              # practice departments, people and one memo per case
python manage.py seed_memo_scenarios --walk       # …and approve them all, printing the path each one took (✔/✘)
python manage.py seed_memo_scenarios --remove     # delete the practice data
```

`seed_memo_scenarios` creates four practice directorates:
- **Alpha** and **Bravo:** director, deputy, line head and two officers each;
- **Charlie:** officers only, no director, to show a waiting memo;
- **Delta:** a director and an officer only.

It sends 16 memos, one per case, and prints where each one is waiting. Every practice account's password is **`Memo-Scenario-2026!`**, at `…@memo-scenarios.test`, for example `alpha.officer@memo-scenarios.test`. Sign in as the person a memo waits on and approve it to watch it move.

The directorate rules sit at priorities chosen to work alongside the routes from `seed_memo_demo` and anything you created in the admin:
- the ES rules (300–310) and the Staff → ES gate (100) still win for memos to the ES;
- otherwise the directorate rules decide (director rules 250–260, staff rules 85–95).

### Tests

`messaging/test_memo_directorate_routing.py` has 36 scenario tests. Each sends a real memo, approves it as whoever it waits on, and checks the exact path and who received it. They cover:
- every row of the rules table;
- departments with no line head, deputy or director;
- a co-director;
- a department head with an ordinary role, and a director by designation only;
- lists spanning several departments;
- return/resubmit and reject at the receiving director;
- only the waiting person being able to act;
- draft previews, the memo page, the seed commands, and coexistence with `seed_memo_demo`.

```bash
python -m pytest messaging            # 75 tests: your 39 + 36 new
```

---

## 16 October 2026 review (earlier)


This is the 16 October 2026 memo review. Everything here is covered by `messaging/test_memo_routing.py`
(23 scenario tests). The rest of the messaging tests also pass.

## What was broken

| # | Symptom you saw | Cause | Fix |
|---|---|---|---|
| 1 | Staff → ES memo stuck on **Line Head** with "No one is assigned", even though the step was optional | In your database the step was set to the **Deputy Director** kind, not the line-head kind. Separately, skipped steps were never recorded, so the page showed them as "No matching actor". The line-head lookup also returned the office head's *staff record* instead of their account, so an office head could never act. | Every skip is now written to the memo's history with its reason ("Optional step — no office head or division head in Finance"). The page shows *Skipped*. `seed_memo_demo` sets the step back to the line-head kind. The office head's account is used. |
| 2 | After deleting the step: "director not approved… no department director", but Finance has a director | Directors were found only by **role code + account department**. Department head, staff-record department and nominal-roll designation were all ignored. A memo already waiting on a step was never looked at again after the route changed. Deleting a step that had decisions was blocked (`PROTECT`). | The director is found from the Department head, then the Director role in the department (account *or* staff record), then a Director / Acting Director designation. Waiting memos are re-checked automatically when a route, step, rule, user, department, office or staff record changes. They are also re-checked when the memo is opened, and from a **Re-check routing** button. Steps can be deleted: the history keeps the step's name. |
| 3 | "Staff to Director" route never used | Both rules matched every memo to one person (no target roles), and "Staff to ES" won on priority 100. | The rules now target `executive-secretary` and `director`. Rules also match on what a person *is*, not only their sign-in role: designation (ES / Director / Deputy) and being a department head both count. |
| 4 | Clicking a memo in a notification gave Django's plain "Page not found" | Notification links were built as `/messaging/memos/<id>/`, but the app lives at `/messages/`. A deleted memo also gave a bare 404. | Links use the real URL. Old `/messaging/...` links redirect. A deleted memo shows "That memo is no longer available — it may have been deleted" on the memo list. People who simply may not see a memo still get the 404. |
| 5 | With `DEBUG=False` the pages lost their styling | `runserver` stops serving `/static/` when `DEBUG` is off. | In development (`settings.dev`), static and media files are still served when `DEBUG=False` (`SERVE_FILES_LOCALLY`, on by default). Production is unchanged: nginx serves them. The styled 404/403/500 pages now look right. |
| 6 | Theme didn't follow the phone / computer dark mode, and the toggle showed the wrong state | Every page load stored the theme, so the device setting was never followed again. The toggle updated before the animated switch finished. | The page follows the device setting. Pressing the toggle overrides it until the device setting next changes. The toggle icon follows both. |
| 7 | Superusers ranked above everyone | A superuser's rank was 1000 regardless of their post, so their memos skipped every approver. | Rank comes from the role and the designation. A department head counts as director rank. |
| 8 | Awaiting list order | — | Urgent first, then High, then the most recently updated. |

## What a memo page now shows

The approval chain lists every step with its state:
- **Approved**, **Returned** or **Rejected**, with who and when;
- **Skipped**, with the reason;
- **Awaiting**, with who it is waiting on;
- **Nobody can act**, with what to fix;
- **Pending**, with who will action it;
- **Not reached**.

Drafts show the *expected* chain before they are sent.

When nobody can act on a step:
- the banner says what to fix ("No director in Human Resources: set the department's head, give someone the Director role, or import their DIRECTOR designation");
- admins are notified once;
- a superuser can act on the step (an override note is recorded);
- anyone with *manage memo routes* can press **Re-check routing**.

## Routes and priorities (after `seed_memo_demo`)

The highest priority rule that matches wins. An empty sender/target list means "anyone".

| Priority | Rule | Memo | Route → steps |
|---|---|---|---|
| 300 | ES writes to anyone / a list | from the ES | Executive Secretary to Staff → delivered directly |
| 200 | Director/Deputy to ES | director or deputy → ES | Director to ES → delivered directly |
| 100 | Staff memo to ES | anyone → ES | Staff to ES → Line Head (optional) → Director (required) → ES |
| 90 | Staff memo to Director | anyone → a director | Staff to Director → Deputy (optional) → Sender's director |
| 80 | Staff memo to Deputy Director | anyone → a deputy | Staff to Deputy → Line Head (optional) |
| 50 | Department circular | whole department | Director (required) |
| 50 | All-staff / all-directors / roles circular | broadcast | Director (optional) → ES (required) |
| 10 | Any other memo to one person / a list | everything else | General memo → Line Head (optional) |

Steps are skipped automatically when the approver:
- is the sender;
- is junior to the sender;
- is the same rank as the sender, when the route says same rank skips.

The ES step is marked *not hierarchical*, so it is never ranked away.

## Commands

```bash
python manage.py migrate                       # messaging 0007 (+ staff 0007–0011 if not yet applied)
python manage.py seed_memo_demo --dry-run      # show what it would change
python manage.py seed_memo_demo --routes-only  # repairs + routes + rules + permissions, no demo data
python manage.py seed_memo_demo                # …plus 3 demo accounts and 14 “[Demo]” memos
python manage.py seed_memo_demo --remove       # delete the demo memos/accounts and added routes/rules
```

**Demo accounts.** The password for all three is `Demo-Memo-2026!`.
- `ict.linehead@demo.nbte.test`: division head, ICT
- `ict.deputy@demo.nbte.test`: deputy director, ICT
- `hr.officer@demo.nbte.test`: officer, Human Resources

**Permissions granted to roles**
- *Send broadcast memos*: ES, Director, Deputy, Super Admin.
- *Approve memos*: those four plus the division head and office heads.
- *Edit/delete any memo*: ES, Super Admin.
- *Manage memo routes*: Super Admin.

No email is sent: memo alerts are in-app notifications.

The shipped `db.sqlite3` already has all of this applied.

## Demo memos in the shipped database

| Ref | State | Shows |
|---|---|---|
| 00005 | Awaiting, **urgent** | Finance staff → ES. Line head skipped (none in Finance). Waiting for the Finance director. |
| 00006 | Awaiting, high | ICT officer → ES. Line head approved. Waiting for the ICT director. |
| 00007 | Published | Director then ES approved. |
| 00008 | Returned | Returned by the ICT director with a comment. |
| 00009 | Rejected | Rejected by the Finance director with a comment. |
| 00010 | **Nobody can act** | HR has no director. Shows the banner and **Re-check routing**. |
| 00011 / 00012 | Awaiting | Staff to Director route: the sender's director / the deputy. |
| 00013 | Awaiting | Staff to Deputy route: waiting on the line head. |
| 00014 | Published | Department circular (sender is the director, so its step is skipped). |
| 00015 | Published | ES circular to all staff, needs acknowledgement. |
| 00016 | Awaiting | All-staff circular from the deputy: director, then ES. |
| 00017 | Published | Director to ES: no approval steps. |
| 00018 | Draft | Shows the expected chain. |

To see 00010 move on, make someone Director of Human Resources, or set the department's head. The memo
re-checks itself as soon as you save.
