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

  // (a, b] 구간의 평일 수. 시세 기준일이 얼마나 뒤처졌는지는 달력일로 세면 안 된다.
  //
  // 공표 규칙이 "기준일 다음 영업일 13시 이후"라, 아무 문제가 없는 주에도 달력일 차가
  // 이렇게 흔들린다:
  //     화 15:00 → asOf 월 → 1일     월 09:00 → asOf 목 → 4일
  //     월 15:00 → asOf 금 → 3일     화 09:00 → asOf 금 → 4일
  // 즉 정상 범위의 최댓값이 4일이라, 멈춘 배치를 잡을 만큼 좁은 달력일 기준(3일)은
  // 매주 월·화 아침마다 오작동한다. 평일로 세면 같은 상황이 전부 2 이하로 접힌다.
  function weekdaysBetween(a, b) {
    var from = new Date(a);
    var to = new Date(b);
    if (isNaN(from) || isNaN(to)) return NaN;
    var count = 0;
    var cursor = new Date(from.getTime());
    while (cursor < to) {
      cursor.setDate(cursor.getDate() + 1);
      if (cursor > to) break;
      var day = cursor.getDay();
      if (day !== 0 && day !== 6) count += 1;
    }
    return count;
  }

  // 정상인 주의 최댓값이 2영업일이므로 3부터 눈에 띄게 표시한다.
  // 연휴(설·추석 등 5일 휴장)에는 이 값이 정상적으로도 3을 넘길 수 있다. 공휴일 달력을
  // 앱에 싣지 않으므로 그건 구별할 수 없고, 그래서 표시는 **판정이 아니라 사실**로 쓴다
  // — "낡음"이라고 단정하지 않고 며칠인지 적고 연휴 가능성을 함께 말한다.
  // (배치도 같은 자리에서 같은 방식으로 경고한다: "공휴일이면 정상이지만, 매일 반복되면…")
  var QUOTE_STALE_WEEKDAYS = 3;

  // 시세 기준일의 나이. 화면이 뺄셈을 요구하지 않도록 경과를 함께 낸다.
  function quoteAge(asOf, today) {
    if (!asOf || !today) return null;
    var weekdays = weekdaysBetween(asOf, today);
    if (!Number.isFinite(weekdays)) return null;
    var label = weekdays <= 0 ? "최신" : weekdays === 1 ? "1영업일 전" : weekdays + "영업일 전";
    return { weekdays: weekdays, days: daysBetween(asOf, today), stale: weekdays >= QUOTE_STALE_WEEKDAYS, label: label };
  }

  global.Format = {
    won: won,
    pct: pct,
    escapeHtml: escapeHtml,
    daysBetween: daysBetween,
    weekdaysBetween: weekdaysBetween,
    quoteAge: quoteAge,
    QUOTE_STALE_WEEKDAYS: QUOTE_STALE_WEEKDAYS,
  };
})(window);
