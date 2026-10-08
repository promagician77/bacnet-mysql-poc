(function () {
  'use strict';
  var $ = function (s) { return document.querySelector(s); };
  var esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
  var n = function (v) { return v.toLocaleString('en-US'); };
  var mode = 'cov', D = null;

  document.querySelector('nav').addEventListener('click', function (e) {
    var b = e.target.closest('[role=tab]'); if (!b) return;
    document.querySelectorAll('[role=tab]').forEach(function (t) { t.setAttribute('aria-selected', String(t === b)); });
    document.querySelectorAll('.panel').forEach(function (p) { p.hidden = p.id !== b.dataset.tab; });
    window.scrollTo(0, 0);
  });

  fetch('sim.json').then(function (r) { return r.json(); }).then(function (d) {
    D = d;
    var A = d.cov_only, B = d.cov_plus_poll, C = d.config;
    $('#f-points').textContent = C.points + ' / ' + C.devices + ' devices';
    $('#f-cov').textContent = A.coverage_pct.toFixed(2) + '%';
    $('#f-both').textContent = B.coverage_pct.toFixed(2) + '%';
    $('.v.bad .stamp').textContent = A.coverage_pct.toFixed(2) + '%';
    $('.v.good .stamp').textContent = B.coverage_pct.toFixed(2) + '%';
    $('#a-dark').textContent = n(A.dark_point_minutes);
    $('#b-dark').textContent = n(B.dark_point_minutes);
    $('#a-gap').textContent = n(A.longest_gap) + ' min';
    $('#b-gap').textContent = n(B.longest_gap) + ' min';
    $('#a-pts').textContent = A.points_with_gap + ' of ' + C.points;
    $('#b-pts').textContent = B.points_with_gap + ' of ' + C.points;
    $('#c-drop').textContent = n(d.injected.cov_notifications_dropped);
    $('#c-lapse').textContent = n(d.injected.subscription_lapses);
    $('#c-resid').textContent = n(B.dark_point_minutes);

    var hrs = (A.longest_gap / 60).toFixed(1);
    $('#punch').innerHTML = 'Across one simulated day, COV alone left <b>' + n(A.dark_point_minutes) +
      '</b> point-minutes with nothing stored, and the worst single gap ran <b>' + n(A.longest_gap) +
      ' minutes</b> &mdash; ' + hrs + ' hours of a point looking dead. Adding a poll every ' + C.poll_every_min +
      ' minutes brought that to <b>' + n(B.dark_point_minutes) + '</b>.';

    drawStrip();
    spotlight(d);
    sizing();
  }).catch(function (e) { console.error(e); });

  function drawStrip() {
    if (!D) return;
    var rows = D.strips.slice().sort(function (a, b) { return a[mode + '_cov'] - b[mode + '_cov']; });
    var h = '<table class="strip"><tbody>';
    rows.forEach(function (p) {
      var band = p[mode], pc = p[mode + '_cov'], bad = pc < 99.9;
      h += '<tr class="' + (bad ? 'g' : '') + '"><td class="lbl">' + esc(p.name) + ' <span class="dv">' + esc(p.device) + '</span></td>'
         + '<td><div class="cells">' + band.map(function (c) { return '<i class="' + (c ? 'x' : '') + '"></i>'; }).join('') + '</div></td>'
         + '<td class="pc">' + pc.toFixed(1) + '%</td></tr>';
    });
    $('#strip').innerHTML = h + '</tbody></table>';

    var t = '<table><thead><tr><th>Point</th><th>Device</th><th>COV only</th><th>COV + poll</th></tr></thead><tbody>';
    D.strips.slice().sort(function (a, b) { return a.cov_cov - b.cov_cov; }).forEach(function (p) {
      t += '<tr><td>' + esc(p.name) + '</td><td>' + esc(p.device) + '</td><td class="n">' + p.cov_cov.toFixed(2)
         + '%</td><td class="n">' + p.both_cov.toFixed(2) + '%</td></tr>';
    });
    $('#striptable').innerHTML = t + '</tbody></table>';
  }

  document.addEventListener('click', function (e) {
    var b = e.target.closest('.sel button'); if (!b) return;
    mode = b.dataset.m;
    document.querySelectorAll('.sel button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
    drawStrip();
  });

  function spotlight(d) {
    var w = d.strips.slice().sort(function (a, b) { return a.cov_cov - b.cov_cov; })[0];
    $('#spot').innerHTML = '<div class="nm">' + esc(w.name) + '<span>' + esc(w.device) + ' &middot; ' + esc(w.id) + '</span></div>'
      + '<p>This point is healthy all day. It simply does not move much, so it never crosses its COV increment and never reports. '
      + 'Under COV alone the database holds a value for ' + w.cov_cov.toFixed(1) + '% of the day; for the rest of it the point is indistinguishable '
      + 'from one whose device has been unplugged. The reconciliation poll does not make it more accurate &mdash; it makes the silence mean something.</p>'
      + '<div class="pair"><div class="r">COV only<b>' + w.cov_cov.toFixed(1) + '%</b></div>'
      + '<div class="g">COV + poll<b>' + w.both_cov.toFixed(1) + '%</b></div></div>';
  }

  function sizing() {
    [['500', 'points'], ['162,000', 'rows / day'], ['59.1 M', 'rows / year'], ['~3.7 GB', 'per year, with indexes']]
      .forEach(function (x, i) {
        var el = document.createElement('div');
        el.innerHTML = '<b>' + x[0] + '</b><span>' + x[1] + '</span>';
        $('#sizing').appendChild(el);
      });
  }

  fetch('files/schema.sql').then(function (r) { return r.text(); }).then(function (t) {
    var h = esc(t)
      .replace(/(\/\*[\s\S]*?\*\/|--[^\n]*)/g, '<span class="cm">$1</span>')
      .replace(/\b(CREATE|TABLE|VIEW|OR|REPLACE|PRIMARY|KEY|UNIQUE|FOREIGN|REFERENCES|CONSTRAINT|ENGINE|NOT|NULL|DEFAULT|PARTITION|BY|RANGE|COLUMNS|VALUES|LESS|THAN|MAXVALUE|SELECT|FROM|JOIN|LEFT|ON|WHERE|GROUP|HAVING|ORDER|AND|AS|AUTO_INCREMENT)\b/g, '<span class="kw">$1</span>')
      .replace(/\b(INT|SMALLINT|TINYINT|VARCHAR|DATETIME|DOUBLE|DECIMAL|ENUM|UNSIGNED|InnoDB)\b/g, '<span class="ty">$1</span>');
    $('#sqlbox').innerHTML = '<code>' + h + '</code>';
  }).catch(function () {});
})();
