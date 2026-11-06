from django.core.management.base import BaseCommand

from rms import automation


class Command(BaseCommand):
    help = "Run every due automation (alerts, auto reorder, daily report, win-back, salary slips, menu sync). Schedule it every 5 minutes."

    def handle(self, *a, **o):
        out = automation.tick(force=True)
        self.stdout.write(self.style.SUCCESS("Automations: " + (", ".join(f"{k}={v}" for k, v in out.items()) or "nothing due")))
