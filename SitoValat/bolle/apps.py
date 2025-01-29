from django.apps import AppConfig

class BolleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bolle'

    def ready(self):
        from . import signals
