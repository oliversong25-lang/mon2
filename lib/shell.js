// lib/shell.js
// 좌측 내비게이션. 세 화면이 같은 셸을 쓰므로 항목을 여기 한 곳에서만 관리한다.
// 목업 기준으로 홈 / 자산 / 분석 / 목표 관리 / 알림 / 설정을 두되, 이번 단계에서
// 실제로 동작하는 것은 홈·자산·설정이다 — 나머지는 "준비 중"으로 표시하고 누를 수 없게 한다.
// 없는 화면으로 보내 빈 페이지를 띄우느니, 아직 없다는 사실을 그대로 보여준다.
(function (global) {
  "use strict";

  var ITEMS = [
    { id: "home", label: "홈", icon: "⌂", href: "home.html" },
    { id: "assets", label: "자산", icon: "▤", href: "assets.html" },
    { id: "analysis", label: "분석", icon: "◪", soon: true },
    { id: "goals", label: "목표 관리", icon: "◎", soon: true },
    { id: "alerts", label: "알림", icon: "♪", soon: true },
    { id: "settings", label: "설정", icon: "⚙", href: "settings.html" },
  ];

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function nav(activeId) {
    var items = ITEMS.map(function (item) {
      var active = item.id === activeId ? " active" : "";
      var badge = item.soon ? '<span class="nav-badge">준비 중</span>' : "";
      var body = '<span class="nav-icon" aria-hidden="true">' + item.icon + "</span><span>" + escapeHtml(item.label) + "</span>" + badge;
      if (item.soon) return '<span class="nav-item" aria-disabled="true">' + body + "</span>";
      return '<a class="nav-item' + active + '" href="' + item.href + '"' + (active ? ' aria-current="page"' : "") + ">" + body + "</a>";
    }).join("");

    return (
      '<nav class="nav" aria-label="주요 메뉴">' +
      '<div class="nav-brand"><span class="nav-mark">A</span><b>AssetFlow</b></div>' +
      '<div class="nav-list">' + items + "</div>" +
      '<div class="nav-foot"><span class="nav-mark">내</span><span>내 자산</span></div>' +
      "</nav>"
    );
  }

  // 화면마다 <main class="main"> 안쪽만 그리면 되도록 셸을 통째로 씌운다.
  function mount(rootId, activeId, innerHtml) {
    var root = document.getElementById(rootId);
    if (!root) return;
    root.className = "shell";
    root.innerHTML = nav(activeId) + '<main class="main"><div class="main-inner" id="page">' + innerHtml + "</div></main>";
  }

  global.Shell = { ITEMS: ITEMS, nav: nav, mount: mount, escapeHtml: escapeHtml };
})(window);
