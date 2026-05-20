from drf_spectacular.extensions import OpenApiAuthenticationExtension


class SharedJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.authentication.authentication.SharedJWTAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Use PMS access token: Authorization: Bearer <token>",
        }
