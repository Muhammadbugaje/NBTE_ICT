# Give every demo staff account the same password (for walkthroughs).
# Usage: python manage.py shell < scripts/set_demo_passwords.py
import os
from django.conf import settings
from accounts.models import User

if not settings.DEBUG:
    raise SystemExit("Refused: DEBUG is off (this is for demo databases only).")
password = os.environ.get("WALK_PASSWORD", "Walk-Through#2026")
users = User.objects.filter(email__iendswith="@demo.nbte.local")
for u in users:
    u.set_password(password)
    u.must_change_password = False
    u.save(update_fields=["password", "must_change_password"])
print(f"{users.count()} demo account(s) now use the password {password!r}")
