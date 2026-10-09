import os

from django.core.wsgi import get_wsgi_application

# Production by default; dev uses manage.py (which defaults to settings.dev).
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nbte_ict.settings.prod")
application = get_wsgi_application()
