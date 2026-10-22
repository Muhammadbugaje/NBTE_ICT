"""drf-spectacular glue: describe ApiJWTAuthentication as a bearer scheme."""
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.plumbing import build_bearer_security_scheme_object


class ApiJWTScheme(OpenApiAuthenticationExtension):
    target_class = "api.authentication.ApiJWTAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema):
        return build_bearer_security_scheme_object(header_name="AUTHORIZATION", token_prefix="Bearer",
                                                   bearer_format="JWT")
