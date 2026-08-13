// lib/indicators-view.js
// 경제지표 카탈로그를 읽고 화면이 쓰기 좋은 모양으로 다듬는다. 홈 카드와 경제지표
// 탭이 같은 규칙(관측 기간 표기, 만료 판정)을 쓰도록 한 곳에 모았다 — 두 화면이
// 서로 다른 기준으로 "낡았다"고 말하면 어느 쪽을 믿어야 할지 알 수 없다.
(function (global) {
  "use strict";

  var CATALOG_URL = "data/indicators/catalog.json";

  // 월간 지표는 발표까지 한두 달이 걸린다. 그래서 3개월 전 값은 낡은 게 아니라 정상이다.
  // 여섯 달을 넘기면 그건 배치가 멈춘 쪽을 의심해야 하는 신호다.
  var STALE_MONTHS = 6;

  var state = { status: "loading", data: null, error: null };

  function load() {
    return fetch(new URL(CATALOG_URL, document.baseURI))
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (payload) {
        // 파일은 있는데 내용이 비어 있는 경우를 성공으로 넘기지 않는다.
        if (!payload || !Array.isArray(payload.series) || !payload.series.length) {
          state = { status: "empty", data: payload || null, error: "catalog.json에 계열이 없습니다" };
          return state;
        }
        state = { status: "ready", data: payload, error: null };
        return state;
      })
      .catch(function (error) {
        console.error("[경제지표] catalog.json 로드 실패:", error);
        state = { status: "error", data: null, error: error.message };
        return state;
      });
  }

  // "2026-06" / "2026-Q2" / "2026" 세 형태가 온다. 비교용으로 월 단위로 편다.
  function periodToMonths(period) {
    var quarter = /^(\d{4})-Q([1-4])$/.exec(period);
    if (quarter) return Number(quarter[1]) * 12 + Number(quarter[2]) * 3;
    var monthly = /^(\d{4})-(\d{2})$/.exec(period);
    if (monthly) return Number(monthly[1]) * 12 + Number(monthly[2]);
    var yearly = /^(\d{4})$/.exec(period);
    if (yearly) return Number(yearly[1]) * 12 + 12;
    return null;
  }

  function periodLabel(period) {
    var quarter = /^(\d{4})-Q([1-4])$/.exec(period);
    if (quarter) return quarter[1] + "년 " + quarter[2] + "분기";
    var monthly = /^(\d{4})-(\d{2})$/.exec(period);
    if (monthly) return monthly[1] + "년 " + Number(monthly[2]) + "월";
    return String(period);
  }

  function freqLabel(freq) {
    return freq === "Q" ? "분기" : freq === "A" ? "연간" : "월간";
  }

  // 카탈로그가 통째로 낡았는지. 개별 계열이 늦는 것과 배치가 멈춘 것은 다른 사건이다.
  function staleness() {
    if (state.status !== "ready") return null;
    var newest = state.data.newestPeriod;
    var months = periodToMonths(newest);
    if (months === null) return { stale: true, newest: newest, monthsBehind: null };
    var now = new Date();
    var nowMonths = now.getUTCFullYear() * 12 + (now.getUTCMonth() + 1);
    var behind = nowMonths - months;
    return { stale: behind > STALE_MONTHS, newest: newest, monthsBehind: behind };
  }

  function indicatorMeta(id) {
    return (state.data?.indicators || []).find(function (indicator) { return indicator.id === id; }) || null;
  }

  function countryMeta(code) {
    return (state.data?.countries || {})[code] || { ko: code, en: code };
  }

  // 값 하나를 화면에 낼 모양으로. 관측 기간은 반드시 값과 함께 나간다.
  function decorate(entry) {
    var indicator = indicatorMeta(entry.indicator);
    var country = countryMeta(entry.country);
    return {
      id: entry.id,
      indicator: entry.indicator,
      indicatorKo: indicator ? indicator.nameKo : entry.indicator,
      indicatorEn: indicator ? indicator.nameEn : entry.indicator,
      unitKo: indicator ? indicator.unitKo : "",
      country: entry.country,
      countryKo: country.ko,
      countryEn: country.en,
      freq: entry.freq,
      freqKo: freqLabel(entry.freq),
      period: entry.period,
      periodKo: periodLabel(entry.period),
      value: entry.value,
    };
  }

  function all() {
    return (state.data?.series || []).map(decorate);
  }

  // 1차 묶음은 국가다 — 탭에 들어오면 나라가 먼저 보이고 그 안에 지표가 있다.
  function byCountry(query) {
    var rows = search(query);
    var groups = new Map();
    rows.forEach(function (row) {
      if (!groups.has(row.country)) groups.set(row.country, { country: row.country, countryKo: row.countryKo, countryEn: row.countryEn, rows: [] });
      groups.get(row.country).rows.push(row);
    });
    var order = (state.data?.indicators || []).map(function (indicator) { return indicator.id; });
    groups.forEach(function (group) {
      group.rows.sort(function (a, b) { return order.indexOf(a.indicator) - order.indexOf(b.indicator); });
    });
    // 한국·미국을 먼저 보여준다. 나머지는 한글 이름 순.
    var priority = ["KOR", "USA"];
    return [...groups.values()].sort(function (a, b) {
      var pa = priority.indexOf(a.country), pb = priority.indexOf(b.country);
      if (pa !== pb) return (pa < 0 ? 99 : pa) - (pb < 0 ? 99 : pb);
      return a.countryKo.localeCompare(b.countryKo, "ko");
    });
  }

  // 지표명·국가명을 한글과 영문 양쪽에서 찾는다.
  function search(query) {
    var rows = all();
    var needle = String(query || "").trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter(function (row) {
      return [row.indicatorKo, row.indicatorEn, row.countryKo, row.countryEn, row.country]
        .some(function (field) { return String(field).toLowerCase().includes(needle); });
    });
  }

  function find(indicatorId, countryCode) {
    var entry = (state.data?.series || []).find(function (row) {
      return row.indicator === indicatorId && row.country === countryCode;
    });
    return entry ? decorate(entry) : null;
  }

  function formatValue(row) {
    if (!Number.isFinite(row.value)) return "–";
    var digits = row.unitKo && row.unitKo.indexOf("%") === 0 ? 2 : row.unitKo.includes("%") ? 2 : 1;
    return row.value.toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  global.Indicators = {
    CATALOG_URL: CATALOG_URL,
    STALE_MONTHS: STALE_MONTHS,
    get state() { return state; },
    load: load,
    all: all,
    search: search,
    byCountry: byCountry,
    find: find,
    staleness: staleness,
    periodLabel: periodLabel,
    freqLabel: freqLabel,
    formatValue: formatValue,
    attribution: function () { return state.data?.attribution || "OECD"; },
  };
})(window);
