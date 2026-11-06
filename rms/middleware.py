import threading
import zoneinfo

from django.utils import timezone

from .countries import LANGUAGES
from .models import Branch, Setting

_local = threading.local()


def current_currency():
    return getattr(_local, "currency", "Rs")


class ContextMiddleware:
    """Resolves the active branch (master users may switch / view ALL), language, timezone and currency."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from . import audit
        audit.set_actor(request.user, (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", ""))[:45])
        request.branch = None
        request.branch_all = False
        request.setting = None
        user = request.user
        ctx_branch = None
        try:
            request.setting = Setting.get()
        except Exception:  # DB not migrated yet
            return self.get_response(request)

        if user.is_authenticated:
            if user.can_access_all:
                sel = request.session.get("branch")
                if sel and sel != "all":
                    request.branch = Branch.objects.filter(pk=sel, is_active=True).first()
                if not request.branch:
                    request.branch_all = True
                    ctx_branch = Branch.objects.filter(is_master=True).first() or Branch.objects.first()
            else:
                request.branch = user.branch
        ctx_branch = request.branch or ctx_branch
        request.ctx_branch = ctx_branch

        # language
        lang = request.setting.default_language
        if ctx_branch and ctx_branch.language:
            lang = ctx_branch.language
        if request.setting.language_selection_enabled:
            chosen = request.session.get("lang") or (user.language if user.is_authenticated else "")
            if chosen in LANGUAGES:
                lang = chosen
        request.lang = lang if lang in LANGUAGES else "en"

        # timezone + currency
        tzname = ctx_branch.timezone if ctx_branch else "UTC"
        try:
            timezone.activate(zoneinfo.ZoneInfo(tzname))
        except Exception:
            timezone.deactivate()
        _local.currency = ctx_branch.currency_symbol if ctx_branch else "Rs"

        response = self.get_response(request)
        timezone.deactivate()
        return response
