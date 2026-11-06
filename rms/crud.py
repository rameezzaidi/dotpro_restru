"""Tiny generic CRUD factory used by all master-data screens."""
from django.contrib import messages
from django.db.models import ProtectedError, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import Branch
from .permissions import master_required, role_required, scope


def build_crud(*, prefix, model, form_class, title, columns, roles=("manager",), master_only=False, scoped=False,
               base_qs=None, defaults=None, row_actions=(), search=(), subtitle="", icon="list",
               prepare=None, after_save=None):
    """columns: [(label, attr_path, kind)] kind in {None,'bool','money','badge','branch','num'}"""
    deco = master_required if master_only else role_required(*roles)

    def get_qs(request):
        qs = base_qs(request) if base_qs else model._default_manager.all()
        if scoped:
            qs = scope(qs, request)
        return qs

    def make_form(request, obj=None, data=None):
        form = form_class(data, request.FILES, instance=obj) if data is not None else form_class(instance=obj)
        if "branch" in form.fields:
            if request.user.can_access_all:
                form.fields["branch"].queryset = Branch.objects.filter(is_active=True)
                if obj is None and request.branch and not form.is_bound:
                    form.fields["branch"].initial = request.branch
            else:
                del form.fields["branch"]
        if prepare:
            prepare(form, request, obj)
        return form

    @deco
    def list_view(request):
        qs = get_qs(request)
        q = request.GET.get("q", "").strip()
        if q and search:
            cond = Q()
            for f in search:
                cond |= Q(**{f"{f}__icontains": q})
            qs = qs.filter(cond)
        rows = []
        for obj in qs[:500]:
            acts = [{"label": l, "url": reverse(u, args=[obj.pk]), "icon": i} for (l, u, i) in row_actions]
            rows.append({"obj": obj, "actions": acts})
        return render(request, "rms/crud_list.html", {
            "title": title, "subtitle": subtitle, "columns": columns, "rows": rows, "q": q,
            "searchable": bool(search), "add_url": reverse(f"{prefix}_add"), "edit_name": f"{prefix}_edit",
            "delete_name": f"{prefix}_delete", "icon": icon})

    @deco
    def add_view(request):
        if request.method == "POST":
            form = make_form(request, None, request.POST)
            if form.is_valid():
                obj = form.save(commit=False)
                for k, v in (defaults or {}).items():
                    setattr(obj, k, v)
                if scoped and hasattr(obj, "branch_id") and not request.user.can_access_all:
                    obj.branch = request.branch
                obj.save()
                form.save_m2m()
                if after_save:
                    after_save(obj, request, True)
                messages.success(request, f"{title}: saved.")
                return redirect(prefix)
        else:
            form = make_form(request)
        return render(request, "rms/crud_form.html", {"title": f"Add — {title}", "form": form, "back": reverse(prefix)})

    @deco
    def edit_view(request, pk):
        obj = get_object_or_404(get_qs(request), pk=pk)
        if request.method == "POST":
            form = make_form(request, obj, request.POST)
            if form.is_valid():
                obj = form.save(commit=False)
                if scoped and hasattr(obj, "branch_id") and not request.user.can_access_all:
                    obj.branch = request.branch
                obj.save()
                form.save_m2m()
                if after_save:
                    after_save(obj, request, False)
                messages.success(request, f"{title}: updated.")
                return redirect(prefix)
        else:
            form = make_form(request, obj)
        return render(request, "rms/crud_form.html", {"title": f"Edit — {title}", "form": form, "back": reverse(prefix),
                                                      "obj": obj})

    @deco
    @require_POST
    def delete_view(request, pk):
        obj = get_object_or_404(get_qs(request), pk=pk)
        try:
            obj.delete()
            messages.success(request, "Deleted.")
        except ProtectedError:
            messages.error(request, "This record is in use and cannot be deleted. Deactivate it instead.")
        return redirect(prefix)

    return {"list": list_view, "add": add_view, "edit": edit_view, "delete": delete_view}
