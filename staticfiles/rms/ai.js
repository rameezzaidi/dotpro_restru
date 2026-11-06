/* Restopro AI helpers: free-text ordering and "frequently bought together" strip. */
window.RestoAI = (function () {
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }

  function order(cfg) {
    var inp = document.getElementById(cfg.input), btn = document.getElementById(cfg.button), out = document.getElementById(cfg.output);
    if (!inp || !btn) return;
    function go() {
      var t = inp.value.trim(); if (!t) return;
      btn.disabled = true; out.textContent = "…";
      var fd = new FormData(); fd.append("text", t);
      fetch(cfg.url, {method: "POST", body: fd, credentials: "same-origin", headers: {"X-CSRFToken": cfg.csrf()}})
        .then(function (r) { return r.json(); })
        .then(function (d) {
          btn.disabled = false;
          if (!d.ok || !d.lines.length) { out.innerHTML = '<span class="ai-bad">Could not match any item. Try: "2 zinger burger aur 1 coke"</span>'; return; }
          d.lines.forEach(function (l) { cfg.add(l); });
          inp.value = "";
          out.innerHTML = '<span class="ai-ok">Added: ' + d.lines.map(function (l) { return l.qty + " × " + esc(l.name); }).join(", ") + "</span>" +
            (d.unmatched.length ? ' <span class="ai-bad">· not understood: ' + esc(d.unmatched.join(", ")) + "</span>" : "");
        }).catch(function () { btn.disabled = false; out.textContent = "Network error"; });
    }
    btn.addEventListener("click", go);
    inp.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); go(); } });
  }

  var timer = null;
  function upsell(cfg) {
    var strip = document.getElementById(cfg.strip);
    if (!strip) return {refresh: function () {}};
    strip.addEventListener("click", function (e) { var b = e.target.closest("[data-up]"); if (b) cfg.add(parseInt(b.dataset.up)); });
    return {refresh: function () {
      clearTimeout(timer);
      var ids = cfg.ids();
      if (!ids.length) { strip.innerHTML = ""; return; }
      timer = setTimeout(function () {
        fetch(cfg.url + "?ids=" + ids.join(","), {credentials: "same-origin"}).then(function (r) { return r.json(); }).then(function (d) {
          strip.innerHTML = d.items.length ? '<div class="ai-up-h">✨ Goes well with this</div>' + d.items.map(function (i) {
            return '<button type="button" class="ai-chip" data-up="' + i.id + '" title="' + esc(i.reason) + '">+ ' + esc(i.name) + " <b>" + cfg.fmt(i.price) + "</b></button>"; }).join("") : "";
        }).catch(function () {});
      }, 350);
    }};
  }
  return {order: order, upsell: upsell};
})();
