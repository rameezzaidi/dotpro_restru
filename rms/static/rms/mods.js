/* Modifier chooser (size / extras) shared by the POS and the website.
   askMods(menuItem, fmt, callback) -> callback({ids:[...], text:"Large, Extra cheese", delta:150}) */
(function () {
  var esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); };
  window.askMods = function (m, fmt, cb) {
    var old = document.getElementById("mods-ov"); if (old) old.remove();
    var ov = document.createElement("div"); ov.id = "mods-ov";
    ov.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px";
    var h = '<div style="background:var(--card,#fff);color:var(--text,#222);border-radius:14px;max-width:420px;width:100%;max-height:88vh;overflow:auto;padding:18px;box-shadow:0 20px 50px rgba(0,0,0,.35)">' +
      '<h3 style="margin:0 0 4px">' + esc(m.name) + '</h3><div class="muted small" style="margin-bottom:10px">' + fmt(m.price) + '</div>';
    m.mods.forEach(function (g) {
      h += '<fieldset style="border:0;padding:0;margin:0 0 12px"><legend style="font-weight:600;margin-bottom:4px">' + esc(g.name) +
        ' <span class="muted small">' + (g.required ? "required" : "optional") + (g.multi ? " · pick any" : " · pick one") + '</span></legend>';
      g.options.forEach(function (o) {
        h += '<label style="display:flex;gap:8px;align-items:center;padding:6px 2px;cursor:pointer"><input type="' + (g.multi ? "checkbox" : "radio") + '" name="g' + g.id + '" value="' + o.id +
          '" data-price="' + o.price + '" data-name="' + esc(o.name) + '"> <span style="flex:1">' + esc(o.name) + '</span>' + (o.price ? '<span class="muted">+' + fmt(o.price) + '</span>' : "") + '</label>';
      });
      h += "</fieldset>";
    });
    h += '<div id="mods-err" style="color:#c0392b;min-height:18px;font-size:13px"></div><div style="display:flex;gap:8px;justify-content:flex-end">' +
      '<button type="button" class="btn" id="mods-no">Cancel</button><button type="button" class="btn primary" id="mods-ok">Add to order</button></div></div>';
    ov.innerHTML = h; document.body.appendChild(ov);
    function close() { ov.remove(); }
    ov.addEventListener("click", function (e) { if (e.target === ov) close(); });
    document.getElementById("mods-no").onclick = close;
    document.getElementById("mods-ok").onclick = function () {
      var ids = [], names = [], delta = 0;
      for (var i = 0; i < m.mods.length; i++) {
        var g = m.mods[i], picks = ov.querySelectorAll('input[name="g' + g.id + '"]:checked');
        if (g.required && !picks.length) { document.getElementById("mods-err").textContent = "Please choose " + g.name; return; }
        picks.forEach(function (p) { ids.push(parseInt(p.value)); names.push(p.dataset.name); delta += parseFloat(p.dataset.price) || 0; });
      }
      close(); cb({ids: ids, text: names.join(", "), delta: delta});
    };
  };
  // cart helpers: lines are keyed "id" or "id_opt-opt"; `mid` is always the menu item id
  window.cartQty = function (cart, mid) {
    var n = 0; Object.values(cart).forEach(function (l) { if ((l.mid || l.id) == mid) n += l.qty; }); return n;
  };
  window.cartAdd = function (cart, m, sel, qty) {
    sel = sel || {ids: [], text: "", delta: 0};
    var key = sel.ids.length ? m.id + "_" + sel.ids.slice().sort().join("-") : String(m.id);
    if (!cart[key]) cart[key] = {id: key, mid: m.id, name: m.name + (sel.text ? " (" + sel.text + ")" : ""), price: m.price + sel.delta, qty: 0, mods: sel.ids};
    cart[key].qty += qty || 1; return cart[key];
  };
  window.cartItems = function (cart) {
    return Object.values(cart).map(function (l) { return {id: l.mid || l.id, qty: l.qty, mods: l.mods || []}; });
  };
})();
