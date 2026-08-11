// lib/format.js
// 화면 표기 헬퍼. 홈과 자산 화면이 같은 형식으로 숫자를 찍어야 해서 한곳에 둔다
// (같은 금액이 화면마다 다르게 반올림되면 계산이 틀린 것처럼 보인다).
(function (global) {
  "use strict";

  function won(value) {
    if (!Number.isFinite(value)) return "–";
    var sign = value < 0 ? "-" : "";
    var n = Math.abs(Math.round(value));
    if (n >= 100000000) {
      var eok = Math.floor(n / 100000000);
      var man = Math.floor((n % 100000000) / 10000);
      return sign + eok.toLocaleString("ko-KR") + "억" + (man ? " " + man.toLocaleString("ko-KR") + "만" : "") + "원";
    }
    if (n >= 10000) {
      var manOnly = Math.floor(n / 10000);
      var rest = n % 10000;
      return sign + manOnly.toLocaleString("ko-KR") + "만" + (rest ? " " + rest.toLocaleString("ko-KR") : "") + "원";
    }
    return sign + n.toLocaleString("ko-KR") + "원";
  }

  function pct(value, digits) {
    return (value * 100).toFixed(digits === undefined ? 1 : digits) + "%";
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function daysBetween(a, b) {
    return Math.round((new Date(b) - new Date(a)) / 86400000);
  }

  global.Format = { won: won, pct: pct, escapeHtml: escapeHtml, daysBetween: daysBetween };
})(window);
