from django.core.management.base import BaseCommand, CommandError

from rms.models import Branch, User


class Command(BaseCommand):
    help = ("Create or update a staff user. Example:\n"
            "  python manage.py create_user kitchen1 MyPass123 --role kds --branch KHI --name \"Main Kitchen\"")

    def add_arguments(self, p):
        p.add_argument("username"); p.add_argument("password")
        p.add_argument("--role", default="kitchen", choices=[r for r, _ in User.ROLES],
                       help="kitchen = chef (own orders), kds = kitchen display / head chef (whole board)")
        p.add_argument("--branch", default="", help="branch code, e.g. KHI")
        p.add_argument("--name", default="")

    def handle(self, *a, username, password, role, branch, name, **o):
        b = None
        if branch:
            b = Branch.objects.filter(code=branch.upper()).first()
            if not b:
                raise CommandError("Branch not found. Codes: " + ", ".join(Branch.objects.values_list("code", flat=True)))
        u, created = User.objects.get_or_create(username=username)
        u.role, u.branch, u.is_active = role, b, True
        if name:
            u.first_name = name
        u.set_password(password); u.save()
        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} {username} ({role}) branch={b.code if b else '-'}"))
