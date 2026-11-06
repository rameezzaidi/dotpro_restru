/* Crystal-report style tables: search, per-column filters, sorting, pagination, totals, print and CSV export.
   Applies to every <table class="tbl"> automatically. Opt-outs: data-plain (no changes), data-server (rows are paged by the
   server: only print / CSV), data-nosearch. Totals: put data-sum on a <th>. */
(function () {
  var SIZES = [10, 15, 25, 50, 100, 0];
  var NUM = /^[^\d\-]*(-?\d[\d,]*\.?\d*|-?\.\d+)/;

  function cellText(td) { return (td.innerText || td.textContent || "").trim(); }
  function firstLine(td) { return cellText(td).split("\n")[0].trim(); }
  function num(s) { var m = NUM.exec(s); return m ? parseFloat(m[1].replace(/,/g, "")) : NaN; }
  function fmt(n) { return n.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}); }

  function eligible(t) {
    if (t.hasAttribute("data-plain") || t.closest("dialog") || t.closest("[data-plain]")) return false;
    if (t.querySelector("tbody input, tbody select, tbody textarea")) return false;
    return !!t.tBodies[0] && t.tBodies[0].rows.length > 0;
  }

  function init(t) {
    var tbody = t.tBodies[0];
    var all = Array.prototype.slice.call(tbody.rows);
    var rows = all.filter(function (r) { return !(r.cells.length === 1 && r.cells[0].colSpan > 1); });   // drop "No data" rows
    if (!rows.length) return;
    t.classList.add("crystal");
    var server = t.hasAttribute("data-server");
    var interactive = server || rows.length >= 5;
    var heads = t.tHead ? Array.prototype.slice.call(t.tHead.rows[0].cells) : [];
    var sumCols = [];
    heads.forEach(function (th, i) { if (th.hasAttribute("data-sum")) sumCols.push(i); });
    if (sumCols.length) t.classList.add("has-total");
    var st = {q: "", cols: {}, page: 1, size: parseInt(t.getAttribute("data-size") || "15", 10), sortCol: -1, sortDir: 1, filtered: rows.slice()};

    var wrap = t.closest(".tbl-wrap") || t.parentNode;
    var host = wrap.classList.contains("card") ? wrap : wrap.parentNode;
    var bar, pager, info, pageBox, sizeSel, search, filtersRow, tfoot;

    function title() {
      var card = t.closest(".card"), h = card && card.querySelector(".card-h h2");
      return (h && h.textContent.trim()) || (document.querySelector(".page-head h1") || {}).textContent || document.title;
    }

    function apply() {
      var q = st.q.toLowerCase();
      st.filtered = rows.filter(function (r) {
        if (q && cellText(r).toLowerCase().indexOf(q) < 0) return false;
        for (var c in st.cols) { var cell = r.cells[c]; if (!cell || cellText(cell).toLowerCase().indexOf(st.cols[c].toLowerCase()) < 0) return false; }
        return true;
      });
      st.page = 1;
      render();
    }

    function render() {
      var size = server ? 0 : st.size, total = st.filtered.length;
      var pages = size ? Math.max(1, Math.ceil(total / size)) : 1;
      st.page = Math.min(st.page, pages);
      var from = size ? (st.page - 1) * size : 0, to = size ? from + size : total;
      var show = {};
      st.filtered.forEach(function (r, i) { show[i] = i >= from && i < to; });
      rows.forEach(function (r) { r.style.display = "none"; });
      st.filtered.forEach(function (r, i) { r.style.display = show[i] ? "" : "none"; });
      var zebra = 0;
      st.filtered.forEach(function (r, i) { if (show[i]) { r.classList.toggle("alt", (zebra++ % 2) === 1); } });
      if (info) info.textContent = total ? ("Showing " + (from + 1) + "–" + Math.min(to, total) + " of " + total + (total !== rows.length ? " (filtered from " + rows.length + ")" : "") + " records") : "No matching records";
      if (pageBox) {
        pageBox.innerHTML = "";
        var add = function (label, p, cls) {
          var b = document.createElement("button"); b.type = "button"; b.className = "rpt-pg " + (cls || ""); b.textContent = label;
          if (p < 1 || p > pages) b.disabled = true; else b.onclick = function () { st.page = p; render(); };
          pageBox.appendChild(b);
        };
        add("«", 1); add("‹", st.page - 1);
        var lo = Math.max(1, st.page - 2), hi = Math.min(pages, st.page + 2);
        for (var p = lo; p <= hi; p++) add(String(p), p, p === st.page ? "on" : "");
        add("›", st.page + 1); add("»", pages);
        pager.style.display = pages > 1 || info ? "" : "none";
      }
      if (tfoot) {
        var cells = tfoot.rows[0].cells;
        sumCols.forEach(function (ci) {
          var s = 0, prefix = "";
          st.filtered.forEach(function (r) { var tx = firstLine(r.cells[ci]); var n = num(tx); if (!isNaN(n)) { s += n; if (!prefix) { var m = /^[^\d\-]*/.exec(tx); prefix = (m ? m[0] : "").trim(); } } });
          cells[ci].textContent = (prefix ? prefix + " " : "") + fmt(s);
        });
        cells[0].textContent = (server ? "Page total · " : "Grand total · ") + total + " record" + (total === 1 ? "" : "s");
      }
    }

    function sortBy(ci) {
      st.sortDir = st.sortCol === ci ? -st.sortDir : 1; st.sortCol = ci;
      var dir = st.sortDir;
      rows.sort(function (a, b) {
        var x = firstLine(a.cells[ci] || a), y = firstLine(b.cells[ci] || b), nx = num(x), ny = num(y);
        if (!isNaN(nx) && !isNaN(ny)) return (nx - ny) * dir;
        return x.localeCompare(y, undefined, {numeric: true, sensitivity: "base"}) * dir;
      });
      rows.forEach(function (r) { tbody.appendChild(r); });
      heads.forEach(function (th, i) { th.classList.remove("asc", "desc"); if (i === ci) th.classList.add(dir > 0 ? "asc" : "desc"); });
      apply();
    }

    function visibleData() {
      var head = heads.map(function (th) { return th.textContent.trim(); });
      var skip = head.map(function (h) { return /^(actions?|)$/i.test(h); });
      var out = st.filtered.map(function (r) { return Array.prototype.map.call(r.cells, function (td) { return firstLine(td) === "" ? "" : cellText(td).replace(/\s*\n\s*/g, " · "); }); });
      return {head: head, skip: skip, rows: out};
    }

    function exportCsv() {
      var d = visibleData(), esc = function (s) { return '"' + String(s).replace(/"/g, '""') + '"'; };
      var lines = [];
      if (d.head.length) lines.push(d.head.filter(function (h, i) { return !d.skip[i]; }).map(esc).join(","));
      d.rows.forEach(function (r) { lines.push(r.filter(function (c, i) { return !d.skip[i]; }).map(esc).join(",")); });
      var blob = new Blob(["﻿" + lines.join("\r\n")], {type: "text/csv;charset=utf-8"});
      var a = document.createElement("a"); a.href = URL.createObjectURL(blob);
      a.download = title().replace(/[^\w\-]+/g, "_") + ".csv"; document.body.appendChild(a); a.click(); a.remove();
    }

    function printReport() {
      var d = visibleData(), esc = window.esc || function (s) { return s; };
      var css = (document.querySelector('link[href*="app.css"]') || {}).href || "";
      var body = document.body.dataset, active = [];
      if (st.q) active.push("Search: “" + st.q + "”");
      Object.keys(st.cols).forEach(function (c) { if (st.cols[c] && heads[c]) active.push(heads[c].textContent.trim() + ": “" + st.cols[c] + "”"); });
      var h = '<table class="crystal print-table"><thead><tr>' + d.head.map(function (x, i) { return d.skip[i] ? "" : "<th>" + esc(x) + "</th>"; }).join("") + "</tr></thead><tbody>";
      d.rows.forEach(function (r, ri) {
        h += "<tr" + (ri % 2 ? ' class="alt"' : "") + ">" + r.map(function (c, i) {
          return d.skip[i] ? "" : "<td" + (heads[i] && heads[i].classList.contains("right") ? ' class="right"' : "") + ">" + esc(c) + "</td>";
        }).join("") + "</tr>";
      });
      if (sumCols.length) {
        h += '<tr class="rpt-total">' + heads.map(function (th, i) { return d.skip[i] ? "" : "<td" + (th.classList.contains("right") ? ' class="right"' : "") + ">" + esc(tfoot.rows[0].cells[i].textContent) + "</td>"; }).join("") + "</tr>";
      }
      h += "</tbody></table>";
      var w = window.open("", "_blank", "width=1000,height=800");
      if (!w) { alert("Allow pop-ups to print the report."); return; }
      w.document.write('<!doctype html><html><head><meta charset="utf-8"><title>' + esc(title()) + '</title><link rel="stylesheet" href="' + css + '"><style>body{background:#fff;padding:22px}</style></head><body>' +
        '<div class="rpt-banner"><div><div class="rpt-biz">' + esc(body.business || "Restopro") + '</div><h1>' + esc(title()) + '</h1></div>' +
        '<div class="rpt-meta"><div>Branch: <b>' + esc(body.branch || "—") + '</b></div><div>Printed: ' + new Date().toLocaleString() + '</div><div>By: ' + esc(body.user || "") + '</div><div>Records: <b>' + d.rows.length + "</b></div></div></div>" +
        (active.length ? '<div class="rpt-filters-note">Filters — ' + esc(active.join(" · ")) + "</div>" : "") + h +
        '<div class="rpt-end">— End of report —</div><script>window.onload=function(){setTimeout(function(){window.print()},300)}<\/script></body></html>');
      w.document.close();
    }

    // ---- toolbar
    if (interactive) {
      bar = document.createElement("div"); bar.className = "rpt-bar no-print";
      var left = document.createElement("div"); left.className = "rpt-l";
      if (!server && !t.hasAttribute("data-nosearch")) {
        search = document.createElement("input"); search.type = "search"; search.className = "input rpt-search"; search.placeholder = "Search in table…";
        var timer; search.oninput = function () { clearTimeout(timer); timer = setTimeout(function () { st.q = search.value; apply(); }, 120); };
        left.appendChild(search);
      }
      if (!server && heads.length) {
        var fb = document.createElement("button"); fb.type = "button"; fb.className = "btn sm"; fb.textContent = "Column filters";
        fb.onclick = function () {
          if (filtersRow) { filtersRow.remove(); filtersRow = null; st.cols = {}; apply(); return; }
          filtersRow = document.createElement("tr"); filtersRow.className = "rpt-colfilters";
          heads.forEach(function (th, i) {
            var td = document.createElement("th");
            if (!/^(actions?|)$/i.test(th.textContent.trim())) {
              var inp = document.createElement("input"); inp.className = "input"; inp.placeholder = "filter"; inp.oninput = function () { if (inp.value) st.cols[i] = inp.value; else delete st.cols[i]; apply(); };
              td.appendChild(inp);
            }
            filtersRow.appendChild(td);
          });
          t.tHead.appendChild(filtersRow);
        };
        left.appendChild(fb);
      }
      var right = document.createElement("div"); right.className = "rpt-r";
      if (!server) {
        sizeSel = document.createElement("select"); sizeSel.className = "input rpt-size";
        SIZES.forEach(function (n) { var o = document.createElement("option"); o.value = n; o.textContent = n ? n + " / page" : "All rows"; if (n === st.size) o.selected = true; sizeSel.appendChild(o); });
        sizeSel.onchange = function () { st.size = parseInt(sizeSel.value, 10); st.page = 1; render(); };
        right.appendChild(sizeSel);
      }
      var bp = document.createElement("button"); bp.type = "button"; bp.className = "btn sm"; bp.textContent = "Print"; bp.onclick = printReport;
      var bc = document.createElement("button"); bc.type = "button"; bc.className = "btn sm"; bc.textContent = "CSV"; bc.onclick = exportCsv;
      right.appendChild(bp); right.appendChild(bc);
      bar.appendChild(left); bar.appendChild(right);
      if (host === wrap) wrap.insertBefore(bar, t); else wrap.parentNode.insertBefore(bar, wrap);
      if (!server) {
        pager = document.createElement("div"); pager.className = "rpt-pager no-print";
        info = document.createElement("span"); info.className = "rpt-info"; pageBox = document.createElement("span"); pageBox.className = "rpt-pages";
        pager.appendChild(info); pager.appendChild(pageBox);
        if (host === wrap) wrap.appendChild(pager); else wrap.parentNode.insertBefore(pager, wrap.nextSibling);
      }
      heads.forEach(function (th, i) {
        if (/^(actions?|)$/i.test(th.textContent.trim())) return;
        th.classList.add("sortable"); th.onclick = function () { sortBy(i); };
      });
    }
    if (sumCols.length) {
      tfoot = document.createElement("tfoot"); var tr = document.createElement("tr"); tr.className = "rpt-total";
      heads.forEach(function (th) { var td = document.createElement("td"); if (th.classList.contains("right")) td.className = "right"; tr.appendChild(td); });
      tfoot.appendChild(tr); t.appendChild(tfoot);
    }
    render();
  }

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.forEach.call(document.querySelectorAll("table.tbl"), function (t) { if (eligible(t)) init(t); });
  });
})();
