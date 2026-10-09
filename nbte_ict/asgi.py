import os

from django.core.asgi import get_asgi_application

# Production by default; dev uses manage.py (which defaults to settings.dev).
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nbte_ict.settings.prod")
application = get_asgi_application()
