// lib/shell.js
// 좌측 내비게이션. 세 화면이 같은 셸을 쓰므로 항목을 여기 한 곳에서만 관리한다.
// 목업 기준으로 홈 / 자산 / 분석 / 목표 관리 / 알림 / 설정을 두되, 이번 단계에서
// 실제로 동작하는 것은 홈·자산·설정이다 — 나머지는 "준비 중"으로 표시하고 누를 수 없게 한다.
// 없는 화면으로 보내 빈 페이지를 띄우느니, 아직 없다는 사실을 그대로 보여준다.
(function (global) {
  "use strict";

  var GROUP_ORDER = ["cash", "savings", "equity", "fund", "bond", "crypto", "commodity", "realestate"];
  var GROUP_LABELS = {
    cash: "현금",
    savings: "예금·적금",
    equity: "주식·ETF",
    fund: "펀드",
    bond: "채권",
    crypto: "가상자산",
    commodity: "원자재·실물자산",
    realestate: "부동산",
  };

  var ITEMS = [
    { id: "home", label: "홈", icon: "⌂", href: "home.html" },
    { id: "assets", label: "자산", icon: "▤", expandable: true },
    { id: "analysis", label: "분석", icon: "◪", soon: true },
    { id: "goals", label: "목표 관리", icon: "◎", soon: true },
    { id: "alerts", label: "알림", icon: "♪", soon: true },
    { id: "settings", label: "설정", icon: "⚙", href: "settings.html" },
  ];

  // 자산 하위 목록의 펼침 상태. 페이지를 옮기면 스크립트가 다시 로드되므로,
  // 자산 화면에서는 기본으로 펼쳐 둔다(지금 보고 있는 자산군이 어디인지 보여야 한다).
  var expanded = false;

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  // 보유 건수는 자산군마다 0이어도 목록에서 빼지 않는다. 보유하지 않은 자산군을 숨기면
  // "무엇을 더 넣을 수 있는지" 알 수 없다 — 목록 자체가 입력 안내 역할을 한다.
  function groupList(counts, activeGroup) {
    return (
      '<div class="nav-sub" data-nav-sub>' +
      GROUP_ORDER.map(function (id) {
        var count = (counts && counts[id]) || 0;
        var active = id === activeGroup ? " active" : "";
        return (
          '<a class="nav-subitem' + active + '" href="assets.html#group=' + id + '" data-nav-group="' + id + '">' +
          "<span>" + escapeHtml(GROUP_LABELS[id]) + "</span>" +
          '<span class="nav-count' + (count ? "" : " zero") + '">' + count + "</span>" +
          "</a>"
        );
      }).join("") +
      "</div>"
    );
  }

  function nav(activeId, options) {
    var opts = options || {};
    var counts = opts.assetCounts;
    var isOpen = expanded || activeId === "assets";

    var items = ITEMS.map(function (item) {
      var active = item.id === activeId ? " active" : "";
      var badge = item.soon ? '<span class="nav-badge">준비 중</span>' : "";
      var body = '<span class="nav-icon" aria-hidden="true">' + item.icon + "</span><span>" + escapeHtml(item.label) + "</span>" + badge;

      if (item.soon) return '<span class="nav-item" aria-disabled="true">' + body + "</span>";

      // 자산은 이동 대신 하위 목록을 펼친다 — 자산군을 골라야 갈 곳이 정해진다.
      if (item.expandable) {
        return (
          '<button type="button" class="nav-item nav-toggle' + active + '" data-nav-toggle aria-expanded="' + isOpen + '">' +
          body +
          '<span class="nav-caret" aria-hidden="true">' + (isOpen ? "⌃" : "⌄") + "</span>" +
          "</button>" +
          (isOpen ? groupList(counts, opts.activeGroup) : "")
        );
      }

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

  // 자산군별 보유 건수. 시세를 못 받은 자산도 세어야 한다 — 목록에서 사라지면
  // 그 자산이 없는 것처럼 보인다.
  function countByGroup(assets) {
    var counts = {};
    GROUP_ORDER.forEach(function (id) {
      counts[id] = 0;
    });
    (assets || []).forEach(function (asset) {
      if (asset && counts[asset.group] !== undefined) counts[asset.group] += 1;
    });
    return counts;
  }

  // 화면마다 <main class="main"> 안쪽만 그리면 되도록 셸을 통째로 씌운다.
  function mount(rootId, activeId, innerHtml, options) {
    var root = document.getElementById(rootId);
    if (!root) return;
    root.className = "shell";
    root.innerHTML = nav(activeId, options) + '<main class="main"><div class="main-inner" id="page">' + innerHtml + "</div></main>";
    var toggle = root.querySelector("[data-nav-toggle]");
    if (toggle) {
      toggle.addEventListener("click", function () {
        expanded = !(expanded || activeId === "assets");
        mount(rootId, activeId, innerHtml, options);
      });
    }
  }

  global.Shell = {
    ITEMS: ITEMS,
    GROUP_ORDER: GROUP_ORDER,
    GROUP_LABELS: GROUP_LABELS,
    nav: nav,
    mount: mount,
    countByGroup: countByGroup,
    escapeHtml: escapeHtml,
  };
})(window);
