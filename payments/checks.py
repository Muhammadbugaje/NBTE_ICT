"""T-SEC-8 (v2.1 F.4): production fails fast when a payment gateway is
switched on (qa.payment.<provider>.enabled) without its keys in the
environment. Runs with every ``manage.py`` command that performs system
checks (runserver, migrate, check --deploy) whenever DEBUG is off and the
simulator is off — i.e. in production. The ICT officer sees exactly which
.env names are missing (see .env.example and PAYMENTS_LIVE_TEST.md)."""
from django.conf import settings
from django.core import checks


@checks.register(checks.Tags.security, deploy=False)
def gateway_keys_check(app_configs, force=False, **kwargs):
    if not force and (settings.DEBUG or getattr(settings, "QA_PAYMENTS_SIMULATE", False)):
        return []
    try:
        from . import providers

        rows = [(p, p.missing_env()) for p in providers.PROVIDERS.values() if p.online and p.setting_enabled()]
    except Exception:  # database not ready (first migrate): the settings cannot be read yet
        return []
    errors = []
    for provider, missing in rows:
        if missing:
            errors.append(checks.Error(
                f"{provider.label} is switched on (qa.payment.{provider.code}.enabled) but these environment "
                f"variables are empty: {', '.join(missing)}.",
                hint="Add them to .env (see .env.example / PAYMENTS_LIVE_TEST.md) or switch the provider off "
                     "in QA settings → Payment methods.",
                id=f"payments.E00{1 if provider.code == 'remita' else 2}"))
    return errors
