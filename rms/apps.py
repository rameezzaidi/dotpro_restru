from django.apps import AppConfig


class RmsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rms"
    verbose_name = "Restaurant Management"

    def ready(self):
        from . import audit
        audit.connect()
