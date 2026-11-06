import json

from django.contrib.auth import views as auth_views
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from .. import audit
from ..models import AuditLog, Setting
from ..permissions import role_required, scope


class SafeLoginView(auth_views.LoginView):
    """Login with a lockout: 5 wrong passwords for a username -> 15 minutes pause."""
    template_name = "rms/login.html"
    redirect_authenticated_user = True

    def post(self, request, *a, **kw):
        name = request.POST.get("username", "").strip()
        left = audit.lock_left(name, "") if name else 0
        if left:
            form = self.get_form()
            ctx = self.get_context_data(form=form)
            ctx["locked"] = left
            return self.render_to_response(ctx, status=429)
        return super().post(request, *a, **kw)


@role_required("manager")
def audit_log(request):
    qs = AuditLog.objects.select_related("user", "branch")
    if not request.branch_all:
        qs = qs.filter(Q(branch=request.branch) | Q(branch__isnull=True, user__isnull=True))
    f = {k: request.GET.get(k, "") for k in ("action", "model", "q")}
    if f["action"]:
        qs = qs.filter(action=f["action"])
    if f["model"]:
        qs = qs.filter(model=f["model"])
    if f["q"]:
        qs = qs.filter(Q(username__icontains=f["q"]) | Q(object_repr__icontains=f["q"]) | Q(changes__icontains=f["q"]))
    models_ = list(AuditLog.objects.exclude(model="").values_list("model", flat=True).distinct().order_by("model"))
    return render(request, "rms/audit_log.html", {"rows": qs[:600], "f": f, "models": models_,
                                                  "actions": ["created", "changed", "deleted", "login", "login_failed", "logout"]})


# ---------------------------------------------------------------- PWA (install on the phone)
@never_cache
def manifest(request):
    name = Setting.get().business_name or "Restopro"
    data = {"name": name, "short_name": name[:12], "start_url": "/", "display": "standalone", "background_color": "#f6f1ea",
            "theme_color": "#b4532a", "description": "Restaurant POS and management",
            "icons": [{"src": "/static/rms/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/static/rms/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}
    return HttpResponse(json.dumps(data), content_type="application/manifest+json")


SW = """
const V = 'restopro-v1', ASSETS = ['/offline/'];
self.addEventListener('install', e => { e.waitUntil(caches.open(V).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener('fetch', e => {
  const r = e.request; if (r.method !== 'GET') return;
  const u = new URL(r.url);
  if (u.pathname.startsWith('/static/')) {            // static files: cache first
    e.respondWith(caches.match(r).then(m => m || fetch(r).then(x => { const c = x.clone(); caches.open(V).then(k => k.put(r, c)); return x; })));
  } else if (r.mode === 'navigate') {                 // pages: always live, friendly page when offline
    e.respondWith(fetch(r).catch(() => caches.match('/offline/')));
  }
});
"""


def service_worker(request):
    resp = HttpResponse(SW, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    resp["Cache-Control"] = "no-cache"
    return resp


def offline(request):
    return HttpResponse("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><body style='font-family:system-ui;display:grid;place-items:center;min-height:90vh;background:#f6f1ea;color:#2b2217;text-align:center'>"
                        "<div><h1 style='color:#b4532a'>You are offline</h1><p>Check the internet connection - this page will work again as soon as it is back.</p>"
                        "<button onclick='location.reload()' style='padding:10px 20px;border:0;border-radius:10px;background:#b4532a;color:#fff;font-weight:700'>Try again</button></div></body>")


def media_db(request, pk):
    from django.http import Http404
    from django.views.decorators.http import condition  # noqa: F401

    from ..models import MediaFile
    m = MediaFile.objects.filter(pk=pk).only("data", "content_type").first()
    if not m:
        raise Http404
    resp = HttpResponse(bytes(m.data), content_type=m.content_type)
    resp["Cache-Control"] = "public, max-age=31536000, immutable"       # a new upload gets a new id
    return resp
