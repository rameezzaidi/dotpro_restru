"""Audit trail: records who created / changed / deleted master data, plus logins. Wired through Django signals."""
import threading

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_delete, post_save, pre_save
from django.utils import timezone

_local = threading.local()

SKIP_FIELDS = {"last_login", "updated_at", "id"}
SECRET_FIELDS = {"password"}


def set_actor(user, ip=""):
    _local.user, _local.ip = (user if getattr(user, "is_authenticated", False) else None), ip


def actor():
    return getattr(_local, "user", None), getattr(_local, "ip", "")


def _fmt(v):
    s = "" if v is None else str(v)
    return s if len(s) <= 60 else s[:57] + "..."


def write(action, instance=None, changes="", user=None, username="", ip="", model="", repr_=""):
    from .models import AuditLog
    u, i = actor()
    user = user or u
    try:
        AuditLog.objects.create(
            user=user if getattr(user, "pk", None) else None, username=username or (user.username if user else ""),
            branch=getattr(user, "branch", None) if user else None, action=action,
            model=model or (instance.__class__.__name__ if instance is not None else ""),
            object_repr=(repr_ or (str(instance) if instance is not None else ""))[:160], changes=changes[:2000], ip=ip or i)
    except Exception:           # noqa: BLE001 - the audit must never break the real action
        pass


def _diff(old, new):
    out = []
    for f in new._meta.concrete_fields:
        n = f.attname
        if n in SKIP_FIELDS:
            continue
        a, b = getattr(old, n, None), getattr(new, n, None)
        if a != b:
            out.append(f"{f.verbose_name}: password changed" if n in SECRET_FIELDS else f"{f.verbose_name}: {_fmt(a)} -> {_fmt(b)}")
    return "; ".join(out)


_snap = {}


def connect():
    from .models import (Branch, BranchItem, Category, Channel, Ingredient, MenuItem, PriceRule, Recipe, Rider, Setting,
                         Supplier, Table, User, Expense, Coupon, ModifierGroup)
    models_ = [Expense, Coupon, ModifierGroup, Branch, BranchItem, Category, Channel, Ingredient, MenuItem, PriceRule, Recipe, Rider, Setting, Supplier, Table, User]

    def pre(sender, instance, **kw):
        if instance.pk:
            try:
                _snap[(sender, instance.pk)] = sender._default_manager.get(pk=instance.pk)
            except sender.DoesNotExist:
                pass

    def post(sender, instance, created, **kw):
        if created:
            write("created", instance)
            return
        old = _snap.pop((sender, instance.pk), None)
        d = _diff(old, instance) if old else ""
        if d:
            write("changed", instance, d)

    def gone(sender, instance, **kw):
        write("deleted", instance)

    for m in models_:
        pre_save.connect(pre, sender=m, weak=False, dispatch_uid=f"audit_pre_{m.__name__}")
        post_save.connect(post, sender=m, weak=False, dispatch_uid=f"audit_post_{m.__name__}")
        post_delete.connect(gone, sender=m, weak=False, dispatch_uid=f"audit_del_{m.__name__}")

    def ip_of(request):
        if request is None:
            return ""
        return (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", ""))[:45]

    user_logged_in.connect(lambda sender, request, user, **kw: write("login", user=user, username=user.username, ip=ip_of(request), model="User", repr_=user.username),
                           weak=False, dispatch_uid="audit_login")
    user_logged_out.connect(lambda sender, request, user, **kw: user and write("logout", user=user, username=user.username, ip=ip_of(request), model="User", repr_=user.username),
                            weak=False, dispatch_uid="audit_logout")
    user_login_failed.connect(lambda sender, credentials, request=None, **kw: write(
        "login_failed", username=str((credentials or {}).get("username", ""))[:150], ip=ip_of(request), model="User",
        repr_=str((credentials or {}).get("username", ""))[:150]), weak=False, dispatch_uid="audit_login_failed")


LOCK_AFTER, LOCK_MINUTES = 5, 15


def lock_left(username, ip):
    """Minutes still locked (0 = allowed). Counts failed logins of this username or this IP since the last good login."""
    from datetime import timedelta

    from django.db.models import Q

    from .models import AuditLog
    since = timezone.now() - timedelta(minutes=LOCK_MINUTES)
    q = Q(username__iexact=username)
    last_ok = AuditLog.objects.filter(action="login", created_at__gte=since).filter(Q(username__iexact=username)).first()
    fails = AuditLog.objects.filter(q, action="login_failed", created_at__gte=since)
    if last_ok:
        fails = fails.filter(created_at__gt=last_ok.created_at)
    fails = list(fails.order_by("-created_at")[:LOCK_AFTER])
    if len(fails) >= LOCK_AFTER:
        left = fails[0].created_at + timedelta(minutes=LOCK_MINUTES) - timezone.now()
        return max(1, int(left.total_seconds() // 60) + 1)
    return 0
