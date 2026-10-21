# How an institution gets into the NBTE QA system

This guide answers one question: **where does an institution register, and what happens after that?**

There are three ways in. All three create the same thing: an institution record with the status **Proposed** and a **primary portal account** (the institution's administrator). After that, every institution follows the same road: onboarding (SOP-01) → approval → QA requests (SOP-02) → the internal QA case (SOP-03).

| Way in | Who does it | Where | Second officer? |
|---|---|---|---|
| 1. Self-registration | The institution itself | QA portal → **Register** (`/register/` on the portal host) | No |
| 2. Add TVET | An NBTE officer | Internal system → sidebar **New Institution → Add TVET** | Yes — a second officer confirms |
| 3. Bulk Import (Excel) | An NBTE officer | Internal system → sidebar **New Institution → Bulk Import (Excel)** | Yes — a second officer confirms the batch |

> **The two addresses.** The system answers on two host names. On your own computer they are:
> - **Internal system (NBTE staff):** `http://localhost:8000/`
> - **QA portal (institutions, resource persons, and staff who want the portal dashboard):** `http://qa.localhost:8000/`
>
> Most browsers resolve `*.localhost` to your own computer without any setup. If `qa.localhost` does not open, add the line `127.0.0.1 qa.localhost` to your hosts file (`C:\Windows\System32\drivers\etc\hosts` on Windows, `/etc/hosts` on macOS and Linux). On a server, the portal host comes from the `QA_PORTAL_HOST` environment variable (for example `qa.nbte.gov.ng`).

---

## 1. Self-registration on the portal (the normal route)

Use this when an institution registers itself.

1. Start the server: `python manage.py runserver 0.0.0.0:8000`
2. Open **`http://qa.localhost:8000/register/`** (the portal sign-in page also has a link: *New TVET institution? **Register with NBTE***).
3. Fill in the form:
   - **Institution:** name, ownership (federal / state / private …), institution type, category, state and LGA, address, year established, CAC RC number (private institutions), website.
   - **Contacts:** official e-mail, phone, name of the head of the institution.
   - **Administrator account:** first name, last name, e-mail, job title and a password (typed twice). This person becomes the institution's **primary user**.
4. Press **Register**. The system:
   - creates the institution with the status **Proposed** and gives it a short institution code (shown in the welcome message);
   - creates the administrator's portal account and signs them in;
   - opens **Application** (onboarding) on the portal.

Self-registration can be switched off in **Settings → Rules & thresholds → `qa.portal.self_registration_enabled`**. The form allows five registrations an hour from one network address.

## 2. Add TVET (NBTE staff register an institution)

Use this when NBTE registers the institution on its behalf, for example after a letter or a visit.

1. Sign in to the internal system (`http://localhost:8000/`) as an officer of the handling department (for example DPAP).
2. In the sidebar open **New Institution → Add TVET**.
3. Fill in the same details as the portal form, including the administrator's name and e-mail (no password — the system makes one), and a **reason**.
4. Press **Send for confirmation**. Nothing is created yet: it is a *pending action*.
5. **A second officer** (another officer with the confirm permission) opens **New Institution → Confirm Add TVET** (or **Pending confirmations**), checks the details and presses **Confirm**. Now the institution (Proposed) and the administrator account exist.
6. Give the administrator their sign-in details: open the institution (**Institutions → the institution**) → **Actions → Send Login Credentials**. The system sets a new temporary password and e-mails it to the administrator; in development the e-mail is printed in the server console instead of being sent. The administrator must change the password at first sign-in.

## 3. Bulk Import (many institutions at once)

1. Internal system → **New Institution → Bulk Import (Excel)**.
2. Download the **Institutions template** (and, if needed, the **Programmes template**), fill one row per institution, and upload it.
3. The system checks the whole sheet first — one bad row rejects the batch and nothing changes. Rows update existing records by code or name, so nothing is duplicated.
4. A second officer confirms the batch. The institutions are created as **Proposed**, each with an administrator account.
5. Send each administrator their credentials with **Send Login Credentials** (step 6 above).

---

## After registration: what the institution does next

Everything below happens on the **portal** (`http://qa.localhost:8000/`), signed in as the institution's administrator.

1. **Institution profile and users** (*Institution*): check the details; add sub-accounts (chief executive, academic departments).
2. **Documents** (*Documents*): upload the required documents (the list comes from Settings → Document rules).
3. **Application — onboarding, SOP-01** (*Application*): submit the application. Then:
   - NBTE departments clear it **in parallel** — Legal, Finance, Academic Planning, Physical Planning (internal system → *Applications → Departmental clearance*). Any of them can return it with a remark.
   - **Institutional fee:** the institution pays (Remita / bank transfer / proof of payment) on *Payments*; Finance confirms it (internal system → *Finance (QA)*).
   - **Programme selection:** the institution chooses the programmes it will run; NTIRD clears them; the programme fee is paid and confirmed.
   - **SSQ:** the institution completes the self-study questionnaire.
4. **Approval:** NBTE approves the institution (internal system → the institution → *Actions → Approve*, confirmed by a second officer). The status becomes **Approved**.
5. **QA requests — SOP-02** (portal → *QA requests → New request*): an approved institution asks for accreditation, reaccreditation, etc. for its programmes (reaccreditation only within six months of expiry — Requirement 4). The QA fee is paid, the SSQ is completed per programme and submitted.
6. **QA case — SOP-03 (Phase 4):** when every SSQ is in, a **QA case** opens automatically and appears on the internal **Case Board** (sidebar *QA cases (SOP-03) → Case Board*). From here NBTE staff work through the 17 stages; each stage arrives in the right person's **Tasks inbox** with an **Open in QA** button.

---

## Institutions already in the demo data

You do not need to register anything to try the system — the seed commands create institutions for you:

| Command | Institution | State | What it is for |
|---|---|---|---|
| `python manage.py seed_startup --with-demo` | **Demo Polytechnic** (`admin@demo-poly.local`) and **Demo College of Health** (`admin@demo-health.local`) | Proposed / onboarding | Phase 2 onboarding walkthrough |
| `python manage.py seed_phase4_demo` | **Kaduna Demo Polytechnic (Phase 4)**, code `KDP` (`admin@p4demo.local`), with *Computer Science* and *Electrical/Electronic Engineering Technology*, both accredited and due for reaccreditation | Approved, with a QA request whose SSQs are submitted and a QA case | Phase 4 SOP-03 walkthrough |

The passwords are printed by the command (and recorded in `PHASE4_DEMO_CREDENTIALS.md`). Remove all demo data with `python manage.py seed_startup --reset-demo`.

### Quick try-out: register a brand-new institution yourself

1. `python manage.py runserver 0.0.0.0:8000`
2. Open `http://qa.localhost:8000/register/`, register "Test Polytechnic" with your own e-mail and a password.
3. You land on **Application**. Upload documents, submit.
4. In another browser (or a private window) sign in at `http://localhost:8000/` as an NBTE officer and open **Applications → Departmental clearance** to see it arrive.

## Troubleshooting

| Problem | Fix |
|---|---|
| `qa.localhost` does not open | Add `127.0.0.1 qa.localhost` to your hosts file, or set `QA_PORTAL_HOST` to a name that resolves. |
| "Registration is closed" | Settings → Rules & thresholds → set `qa.portal.self_registration_enabled` to true. |
| Add TVET did nothing | It waits for a second officer: *Pending confirmations*. |
| The administrator never got an e-mail | In development e-mails are printed in the server console. Use *Send Login Credentials* to issue a new password. |
| The institution cannot raise a QA request | It must be **Approved**, and its programmes accredited and within six months of expiry (Requirement 4). |
