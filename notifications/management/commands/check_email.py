"""python manage.py check_email [--send --to someone@example.com]

Checks that outgoing email is really set up, provider by provider, in the
order the system uses them (Brevo SMTP → Brevo API → Gmail):

  * which providers have credentials in the environment / .env
  * the sender address (DEFAULT_FROM_EMAIL) — providers reject unverified senders
  * for each SMTP provider: DNS lookup, TCP connection, STARTTLS and login
    (nothing is sent)
  * how queued email is delivered (Celery eager in development, a worker in
    production)

With --send it then sends one real test message through the same engine the
app uses (notifications.engine.send) and reports which provider accepted it.
"""
import socket

from django.conf import settings
from django.core.mail import get_connection
from django.core.management.base import BaseCommand, CommandError

from notifications import engine


class Command(BaseCommand):
    help = "Check the outgoing email providers (and optionally send a test email)."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true", help="Also send a real test email.")
        parser.add_argument("--to", help="Recipient for --send.")

    def ok(self, msg):
        self.stdout.write(self.style.SUCCESS("  ✓ " + msg))

    def bad(self, msg):
        self.stdout.write(self.style.ERROR("  ✗ " + msg))

    def warn(self, msg):
        self.stdout.write(self.style.WARNING("  ! " + msg))

    def handle(self, *args, send=False, to=None, **opts):
        problems = 0
        self.stdout.write("Sender")
        sender = getattr(settings, "DEFAULT_FROM_EMAIL", "")
        domain = sender.rsplit("@", 1)[-1].strip("> ").lower() if "@" in sender else ""
        if not domain or domain in ("localhost", "example.com") or domain.endswith(".local"):
            self.bad(f"DEFAULT_FROM_EMAIL is {sender!r}. Set it in .env to an address on a domain you "
                     "have verified with Brevo (e.g. NBTE ICT <no-reply@nbte.gov.ng>) — providers refuse "
                     "other senders.")
            problems += 1
        else:
            self.ok(f"DEFAULT_FROM_EMAIL = {sender}")
        self.stdout.write(f"  Site address in emails (SITE_URL): {getattr(settings, 'SITE_URL', '') or '— not set'}")

        self.stdout.write("")
        self.stdout.write("Providers (tried in this order)")
        order = engine.provider_order("test")
        for name in ("brevo_smtp", "brevo", "gmail"):
            label = {"brevo_smtp": "Brevo SMTP relay", "brevo": "Brevo HTTP API", "gmail": "Gmail SMTP"}[name]
            if name not in order:
                self.warn(f"{label}: not configured (credentials missing) — skipped")
                continue
            cfg = settings.EMAIL_PROVIDERS[name]
            if cfg["BACKEND"].endswith("smtp.EmailBackend"):
                problems += 0 if self._check_smtp(label, cfg["OPTIONS"]) else 1
            else:
                self.ok(f"{label}: API key present (checked when a message is sent)")
            used, limit = engine.used_today(name), engine.daily_limit(name)
            self.stdout.write(f"      sent today {used} of {limit}")
        if not order:
            self.bad("No provider is configured. Put BREVO_SMTP_LOGIN + BREVO_SMTP_KEY (and/or "
                     "BREVO_API_KEY, GMAIL_USER + GMAIL_APP_PASSWORD) in .env.")
            problems += 1

        self.stdout.write("")
        self.stdout.write("Delivery")
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            self.ok("Celery runs tasks inline (development): queued emails are sent straight away.")
        else:
            self.warn(f"Emails are queued for a Celery worker (broker {getattr(settings, 'CELERY_BROKER_URL', '?')}). "
                      "Make sure `celery -A nbte_ict worker` is running, or nothing is delivered.")

        if send:
            self.stdout.write("")
            self.stdout.write("Test message")
            if not to:
                raise CommandError("--send needs --to someone@example.com")
            trail = []
            try:
                provider = engine.send(f"{settings.SITE_NAME} email check", [to],
                                       "<p>This is a test from <code>manage.py check_email</code>. "
                                       "If you can read it, outgoing email works.</p>",
                                       "Test from manage.py check_email. Outgoing email works.",
                                       category="test", trail=trail)
            except engine.DeliveryError as exc:
                for t in trail:
                    self.bad(f"{t['provider']}: {t['error']}")
                raise CommandError(f"Not sent: {engine.safe_error_message(exc)}")
            for t in trail:
                (self.ok if t["ok"] else self.bad)(f"{t['provider']}: {'accepted' if t['ok'] else t['error']}")
            self.stdout.write(self.style.SUCCESS(f"Sent to {to} via {provider}. Check the inbox (and spam)."))
            return

        self.stdout.write("")
        if problems:
            raise CommandError(f"{problems} problem(s) found — see above.")
        self.stdout.write(self.style.SUCCESS(
            "Email looks set up. Run with --send --to you@example.com to send a real test message."))

    def _check_smtp(self, label, o):
        host, port = o.get("host"), int(o.get("port") or 587)
        try:
            socket.getaddrinfo(host, port)
        except OSError as exc:
            self.bad(f"{label}: cannot resolve {host} ({exc}) — check the internet connection / DNS")
            return False
        try:
            with socket.create_connection((host, port), timeout=10):
                pass
        except OSError as exc:
            self.bad(f"{label}: cannot reach {host}:{port} ({exc}) — a firewall may block outgoing SMTP")
            return False
        conn = get_connection(backend="django.core.mail.backends.smtp.EmailBackend", fail_silently=False,
                              **o)
        try:
            conn.open()
        except Exception as exc:
            self.bad(f"{label}: {host}:{port} reached, but STARTTLS/login failed — "
                     f"{engine.safe_error_message(exc)}. Check the login and key in .env.")
            return False
        finally:
            try:
                conn.close()
            except Exception:
                pass
        self.ok(f"{label}: {host}:{port} reachable, TLS and login accepted ({o.get('username')})")
        return True
