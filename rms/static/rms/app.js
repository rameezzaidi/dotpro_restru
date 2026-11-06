/* Shared UI helpers */
window.esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); };
window.toast = function (html, ms) {
  var box = document.getElementById("toasts");
  if (!box) { box = document.createElement("div"); box.id = "toasts"; document.body.appendChild(box); }
  var t = document.createElement("div"); t.className = "toast"; t.innerHTML = html; box.appendChild(t);
  setTimeout(function () { t.classList.add("out"); setTimeout(function () { t.remove(); }, 400); }, ms || 6000);
};
(function () {
  var PAL = [["#f6d7c3", "#e3946a"], ["#f4e3b5", "#d9a93d"], ["#d9ebdd", "#7fbb97"], ["#dce6f6", "#7f9fd0"], ["#eadcf4", "#a98acb"], ["#f6d3d3", "#d98282"]];
  var G = {
    cup: '<path d="M18 22h28l-3 30a4 4 0 0 1-4 4H25a4 4 0 0 1-4-4z"/><path d="M46 28h4a5 5 0 0 1 0 10h-5"/><path d="M26 10c0 4 4 4 4 8M34 10c0 4 4 4 4 8"/>',
    cake: '<rect x="14" y="34" width="36" height="20" rx="4"/><path d="M14 44c6 5 10-4 18 0s12 5 18 0"/><path d="M32 34V24"/><circle cx="32" cy="20" r="3"/>',
    pizza: '<path d="M10 20c14-8 30-8 44 0L32 56z"/><circle cx="26" cy="30" r="3"/><circle cx="38" cy="30" r="3"/><circle cx="32" cy="42" r="3"/>',
    burger: '<path d="M12 28c0-12 40-12 40 0z"/><path d="M10 36h44"/><path d="M12 44h40"/><path d="M12 44c0 9 40 9 40 0"/>',
    plate: '<circle cx="36" cy="34" r="18"/><circle cx="36" cy="34" r="9"/><path d="M8 14v10a4 4 0 0 0 8 0V14M12 24v28"/>'
  };
  function glyph(cat) {
    var c = (cat || "").toLowerCase(), g = G.plate;
    if (/drink|beverage|juice|tea|coffee/.test(c)) g = G.cup; else if (/dessert|sweet|cake/.test(c)) g = G.cake;
    else if (/pizza|pasta/.test(c)) g = G.pizza; else if (/burger|wrap|sandwich|fast/.test(c)) g = G.burger;
    return '<svg width="54" height="54" viewBox="0 0 64 64" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" opacity=".92">' + g + '</svg>';
  }
  // one tile for POS + website: photo if the item has one, otherwise a coloured illustration
  window.tileHTML = function (m, price, qty) {
    var pal = PAL[(m.category || 0) % PAL.length];
    var ph = m.img ? '<div class="ph" style="background-image:url(\'' + window.esc(m.img) + '\')"></div>'
                   : '<div class="ph" style="background:linear-gradient(135deg,' + pal[0] + ',' + pal[1] + ')">' + glyph(m.cat_name) + '</div>';
    var out = m.max_qty === 0, low = m.max_qty != null && m.max_qty > 0 && m.max_qty <= 5;
    return '<button type="button" class="tile' + (out ? ' soldout' : '') + '" data-id="' + m.id + '"' + (out ? ' data-out="1"' : '') + '>' + ph +
      (out ? '<span class="badge-stock">Sold out</span>' : (low ? '<span class="badge-stock low">Only ' + m.max_qty + ' left</span>' : (m.low && m.low.length ? '<span class="badge-stock low">Low stock</span>' : ''))) +
      (qty ? '<span class="badge-q">' + qty + '</span>' : '') +
      '<div class="tb"><b>' + (m.veg ? '<span class="veg-dot"></span>' : '') + window.esc(m.name) + '</b>' +
      (m.desc ? '<span class="muted small desc">' + window.esc(m.desc) + '</span>' : '') +
      '<span class="p">' + price + '</span></div></button>';
  };
  // update the little quantity badges on tiles without re-rendering (keeps scroll position)
  window.tileBadges = function (cart) {
    document.querySelectorAll(".tile").forEach(function (t) {
      var n = window.cartQty ? window.cartQty(cart, t.dataset.id) : (cart[t.dataset.id] ? cart[t.dataset.id].qty : 0), b = t.querySelector(".badge-q");
      if (n && !b) { b = document.createElement("span"); b.className = "badge-q"; t.appendChild(b); }
      if (b) { if (n) b.textContent = n; else b.remove(); }
    });
  };
})();
(function () {
  document.addEventListener("click", function (e) {
    var dd = e.target.closest(".dd");
    document.querySelectorAll(".dd.open").forEach(function (d) { if (d !== dd) d.classList.remove("open"); });
    var tg = e.target.closest("[data-dd]");
    if (tg) { tg.closest(".dd").classList.toggle("open"); }
    if (e.target.closest("[data-burger]")) { document.body.classList.toggle("nav-open"); }
    var cp = e.target.closest("[data-copy]");
    if (cp) { navigator.clipboard && navigator.clipboard.writeText(cp.getAttribute("data-copy")); cp.textContent = "Copied"; setTimeout(function(){ cp.textContent = "Copy"; }, 1200); }
    var cf = e.target.closest("[data-confirm]");
    if (cf && !window.confirm(cf.getAttribute("data-confirm"))) { e.preventDefault(); }
  });

  // Country -> auto fill timezone / currency / language
  var cs = document.querySelector("[data-country-select]");
  if (cs && window.COUNTRIES) {
    var apply = function () {
      var c = window.COUNTRIES[cs.value]; if (!c) return;
      var set = function (name, v) { var el = document.querySelector("[name='" + name + "']"); if (el) el.value = v; };
      set("timezone", c.tz); set("currency_code", c.cur); set("currency_symbol", c.sym); set("language", c.lang);
      set("default_language", c.lang);
    };
    cs.addEventListener("change", apply);
  }
  // dynamic line rows (purchase orders / recipes)
  document.querySelectorAll("[data-add-row]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var tpl = document.getElementById(btn.getAttribute("data-add-row"));
      var target = document.getElementById(btn.getAttribute("data-target"));
      target.appendChild(tpl.content.cloneNode(true));
    });
  });
  document.addEventListener("click", function (e) {
    var rm = e.target.closest("[data-remove-row]");
    if (rm) { rm.closest("[data-row]").remove(); }
  });
})();
