"""What each NBTE staff member should do next on the onboarding → QA journey.

``next_actions(user)`` → ``[{title, detail, url, button_label, institution, due,
overdue, kind, method, post_url, reject_url, stage, ssq_id, application_id,
pa_id, since}]``, most urgent first:

    director  assign an officer · clear a programme · approve the institution
    ES        confirm (or reject) the institution approval
    officer   assess a self-study

``method == "post"`` rows act in one click on ``post_url`` (CSRF form);
``reject_url`` (ES) takes a ``remark``. Built by ``accreditation.onboarding_linear``.
"""
from __future__ import annotations


def next_actions(user):
    from .onboarding_linear import next_actions as _next
    try:
        return _next(user)
    except Exception:  # one broken card never breaks a dashboard
        import logging
        logging.getLogger(__name__).exception("journey.next_actions failed")
        return []
