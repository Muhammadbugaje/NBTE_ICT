"""drf-spectacular glue: describe ApiJWTAuthentication as a bearer scheme."""
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.plumbing import build_bearer_security_scheme_object


class ApiJWTScheme(OpenApiAuthenticationExtension):
    target_class = "api.authentication.ApiJWTAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema):
        return build_bearer_security_scheme_object(header_name="AUTHORIZATION", token_prefix="Bearer",
                                                   bearer_format="JWT")


def drop_portal_path_mirror(endpoints, **kwargs):
    """v2.3 Phase 1 (D10): the QA portal is also mounted under /qa-portal/ on the
    internal host (a Render workaround in nbte_ict/urls.py). Its API endpoints
    then appear twice in the schema with the same operationIds (29 collisions),
    which breaks generated Flutter clients. The mirror is left out of the
    schema; the endpoints themselves keep working."""
    return [endpoint for endpoint in endpoints if not endpoint[0].startswith("/qa-portal/")]
