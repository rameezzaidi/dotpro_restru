from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def role_required(*roles):
    """Allow listed roles (superuser/owner always pass)."""
    def deco(view):
        @wraps(view)
        @login_required
        def wrapper(request, *a, **kw):
            u = request.user
            if u.is_superuser or u.role == "owner" or u.role in roles:
                return view(request, *a, **kw)
            messages.error(request, "You do not have permission to open that page.")
            return redirect("dashboard")
        return wrapper
    return deco


def master_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *a, **kw):
        if request.user.can_access_all:
            return view(request, *a, **kw)
        messages.error(request, "Only the owner or master-branch manager can open that page.")
        return redirect("dashboard")
    return wrapper


def scope(qs, request, field="branch"):
    """Restrict a queryset to the active branch (or everything in ALL mode for master users)."""
    if request.branch_all:
        return qs
    return qs.filter(**{field: request.branch})
