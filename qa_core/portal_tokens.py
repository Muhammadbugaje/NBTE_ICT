"""Set-password links for the QA portal.

A distinct salt, so a portal link cannot be replayed on the internal reset
page (and vice versa), and a lifetime read from the setting
``qa.credentials.link_valid_hours`` instead of PASSWORD_RESET_TIMEOUT.
The link is single-use: the token covers the password hash and last login.
"""
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int

import config


def link_valid_hours():
    try:
        return max(1, int(config.get("qa.credentials.link_valid_hours", 72)))
    except (TypeError, ValueError):
        return 72


class PortalTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "qa_core.portal_tokens.PortalTokenGenerator"

    def check_token(self, user, token):
        if not (user and token):
            return False
        try:
            ts_b36, _hash = token.split("-")
            ts = base36_to_int(ts_b36)
        except ValueError:
            return False
        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(self._make_token_with_timestamp(user, ts, secret), token):
                break
        else:
            return False
        return (self._num_seconds(self._now()) - ts) <= link_valid_hours() * 3600


portal_token_generator = PortalTokenGenerator()
