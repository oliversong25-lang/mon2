// lib/indicators-view.js
// 경제지표 카탈로그를 읽고 화면이 쓰기 좋은 모양으로 다듬는다. 홈 카드와 경제지표
// 탭이 같은 규칙(관측 기간 표기, 만료 판정)을 쓰도록 한 곳에 모았다 — 두 화면이
// 서로 다른 기준으로 "낡았다"고 말하면 어느 쪽을 믿어야 할지 알 수 없다.
//
// 파일이 셋으로 나뉜다. 지표가 7개에서 50개로 늘면서 최신값까지 한 파일에 담으면
// 250KB가 되는데, 홈 화면은 네 줄만 필요하고 검색은 이름만 있으면 되기 때문이다.
//
//   index.json            주제·지표·국가 + (지표×국가) 존재 여부 + 헤드라인 값.  항상 받는다.
//   latest/<주제>.json     그 주제 계열의 최신값.                                주제를 열 때.
//   oecd/<dataflow>.json  전 국가 10년 시계열.                                   지표를 펼칠 때.
//
// 검색은 index.json만으로 된다 — 기본 브라우즈 트리에 없는 지표도 이름이 여기 다 있다.
(function (global) {
  "use strict";

  // 인덱스가 둘이다. 배치가 서로의 산출물을 덮어쓰지 않도록 출처별로 나눠 쓰기 때문이고,
  // 한쪽 배치가 죽어도 다른 쪽은 그대로 보여야 하기 때문이다. 화면은 둘을 합쳐 읽는다.
  var INDEX_SOURCES = [
    { key: "oecd", url: "data/indicators/index.json", label: "OECD 월·분기 지표" },
    { key: "daily", url: "data/indicators/index-daily.json", label: "일간 금리" },
  ];
  var BASE = "data/indicators/";

  // 월간 지표는 발표까지 한두 달이 걸린다. 그래서 3개월 전 값은 낡은 게 아니라 정상이다.
  // 여섯 달을 넘기면 그건 배치가 멈춘 쪽을 의심해야 하는 신호다.
  var STALE_MONTHS = 6;

  var state = { status: "loading", data: null, error: null };
  var topics = {};   // 주제 id -> { status, series }
  var history = {};  // 파일 경로 -> { status, series }

  // 지표 파일은 전부 배치가 매일 다시 쓴다. 캐시를 그대로 믿으면 어제 값을 들고
  // 있게 되므로 조건부 요청으로 매번 확인한다(그대로면 304라 본문은 안 받는다).
  function fetchJson(path) {
    return DataFetch.json(path, { fresh: true });
  }

  // 인덱스 둘을 합친다. 한쪽이 비면 **어느 쪽이 비었는지** 남긴다 — 한쪽만 보이면
  // 사용자는 나머지가 원래 없는 줄 안다. 그건 없는 것과 못 받은 것을 뭉치는 것이다.
  function mergeIndexes(parts) {
    var merged = {
      sources: {}, attribution: [], updatedAt: null, newestPeriod: null,
      topics: [], indicators: [], countries: {}, failures: [], missing: [],
      // 홈 카드는 주제 파일을 받지 않고 이 값만으로 그린다. 합칠 때 빠뜨리면
      // 홈의 지표 카드가 통째로 비어 버린다(실제로 그렇게 깨졌다).
      headlineSeries: {},
    };
    parts.forEach(function (part) {
      if (!part.payload) {
        merged.missing.push({ key: part.key, label: part.label, url: part.url, reason: part.error || "비어 있음" });
        return;
      }
      var payload = part.payload;
      Object.assign(merged.sources, payload.sources || {});
      if (payload.attribution) merged.attribution.push(payload.attribution);
      if (!merged.updatedAt || (payload.updatedAt && payload.updatedAt > merged.updatedAt)) merged.updatedAt = payload.updatedAt || merged.updatedAt;
      // 가장 최신 관측은 둘 중 더 최근 것. 일간 계열이 있으면 그쪽이 이긴다.
      if (payload.newestPeriod && (!merged.newestPeriod || payload.newestPeriod > merged.newestPeriod)) merged.newestPeriod = payload.newestPeriod;
      (payload.topics || []).forEach(function (topic) { merged.topics.push(topic); });
      (payload.indicators || []).forEach(function (indicator) { merged.indicators.push(indicator); });
      Object.keys(payload.countries || {}).forEach(function (code) {
        if (!merged.countries[code]) merged.countries[code] = payload.countries[code];
      });
      (payload.failures || []).forEach(function (failure) { merged.failures.push(failure); });
      Object.assign(merged.headlineSeries, payload.headlineSeries || {});
    });
    merged.attribution = merged.attribution.join(" · ");
    return merged;
  }

  function load() {
    return Promise.all(INDEX_SOURCES.map(function (part) {
      return fetchJson(part.url)
        .then(function (payload) {
          var usable = payload && Array.isArray(payload.indicators) && payload.indicators.length;
          return Object.assign({}, part, { payload: usable ? payload : null, error: usable ? null : "지표가 없습니다" });
        })
        .catch(function (error) {
          console.error("[경제지표] " + part.url + " 로드 실패:", error);
          return Object.assign({}, part, { payload: null, error: error.message });
        });
    })).then(function (parts) {
      var merged = mergeIndexes(parts);
      if (!merged.indicators.length) {
        state = { status: "empty", data: merged, error: "인덱스 두 개 모두 지표가 없습니다" };
        return state;
      }
      state = { status: "ready", data: merged, error: null };
      return state;
    });
  }

  // 어느 인덱스가 빠졌는지. 화면이 그 사실을 말할 수 있어야 한다.
  function missingSources() {
    return (state.data && state.data.missing) || [];
  }

  // 주제 하나의 최신값. 이미 받았으면 다시 받지 않는다.
  function loadTopic(topicId) {
    if (topics[topicId] && topics[topicId].status !== "error") return Promise.resolve(topics[topicId]);
    var meta = (state.data && state.data.topics || []).find(function (topic) { return topic.id === topicId; });
    if (!meta) return Promise.resolve({ status: "error", series: {}, error: "알 수 없는 주제: " + topicId });
    topics[topicId] = { status: "loading", series: {} };
    return fetchJson(BASE + meta.file)
      .then(function (payload) {
        topics[topicId] = { status: "ready", series: payload.series || {} };
        return topics[topicId];
      })
      .catch(function (error) {
        console.error("[경제지표] " + meta.file + " 로드 실패:", error);
        topics[topicId] = { status: "error", series: {}, error: error.message };
        return topics[topicId];
      });
  }

  function loadTopics(ids) {
    return Promise.all((ids || []).map(loadTopic));
  }

  // 지표 하나의 10년 시계열. dataflow 단위 파일이라 같은 계열의 다른 지표도 같이 온다.
  function loadHistory(indicatorId) {
    var meta = indicatorMeta(indicatorId);
    if (!meta) return Promise.resolve({ status: "error", series: [], error: "알 수 없는 지표: " + indicatorId });
    var path = meta.file;
    if (history[path] && history[path].status !== "error") return Promise.resolve(history[path]);
    history[path] = { status: "loading", series: [] };
    return fetchJson(BASE + path)
      .then(function (payload) {
        history[path] = { status: "ready", series: payload.series || [] };
        return history[path];
      })
      .catch(function (error) {
        console.error("[경제지표] " + path + " 로드 실패:", error);
        history[path] = { status: "error", series: [], error: error.message };
        return history[path];
      });
  }

  function historyOf(indicatorId, countryCode) {
    var meta = indicatorMeta(indicatorId);
    if (!meta || !history[meta.file] || history[meta.file].status !== "ready") return null;
    var id = seriesId(indicatorId, countryCode);
    var row = history[meta.file].series.find(function (entry) { return entry.id === id; });
    return row ? row.observations : null;
  }

  // "2026-06" / "2026-Q2" / "2026" 세 형태가 온다. 비교용으로 월 단위로 편다.
  function periodToMonths(period) {
    // 일간 계열이 들어오면서 YYYY-MM-DD가 생겼다. 이걸 못 읽으면 null이 되고
    // staleness()가 전부 "오래됨"으로 판정한다(실제로 홈 지표 카드가 그렇게 사라졌다).
    var daily = /^(\d{4})-(\d{2})-\d{2}$/.exec(period);
    if (daily) return Number(daily[1]) * 12 + Number(daily[2]);
    var quarter = /^(\d{4})-Q([1-4])$/.exec(period);
    if (quarter) return Number(quarter[1]) * 12 + Number(quarter[2]) * 3;
    var monthly = /^(\d{4})-(\d{2})$/.exec(period);
    if (monthly) return Number(monthly[1]) * 12 + Number(monthly[2]);
    var yearly = /^(\d{4})$/.exec(period);
    if (yearly) return Number(yearly[1]) * 12 + 12;
    return null;
  }

  function periodLabel(period) {
    var daily = /^(\d{4})-(\d{2})-(\d{2})$/.exec(period);
    if (daily) return daily[1] + "년 " + Number(daily[2]) + "월 " + Number(daily[3]) + "일";
    var quarter = /^(\d{4})-Q([1-4])$/.exec(period);
    if (quarter) return quarter[1] + "년 " + quarter[2] + "분기";
    var monthly = /^(\d{4})-(\d{2})$/.exec(period);
    if (monthly) return monthly[1] + "년 " + Number(monthly[2]) + "월";
    return String(period);
  }

  function freqLabel(freq) {
    return freq === "D" ? "일간" : freq === "Q" ? "분기" : freq === "A" ? "연간" : "월간";
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

  function topicList() {
    return (state.data && state.data.topics) || [];
  }

  function topicMeta(id) {
    return topicList().find(function (topic) { return topic.id === id; }) || null;
  }

  function indicatorList() {
    return (state.data && state.data.indicators) || [];
  }

  function indicatorMeta(id) {
    return indicatorList().find(function (indicator) { return indicator.id === id; }) || null;
  }

  function countryMeta(code) {
    return ((state.data && state.data.countries) || {})[code] || { ko: code, en: code };
  }

  function countryList() {
    var countries = (state.data && state.data.countries) || {};
    // 한국·미국을 먼저. 나머지는 한글 이름 순.
    var priority = ["KOR", "USA"];
    return Object.keys(countries).sort(function (a, b) {
      var pa = priority.indexOf(a), pb = priority.indexOf(b);
      if (pa !== pb) return (pa < 0 ? 99 : pa) - (pb < 0 ? 99 : pb);
      return countries[a].ko.localeCompare(countries[b].ko, "ko");
    }).map(function (code) {
      return { code: code, ko: countries[code].ko, en: countries[code].en };
    });
  }

  function seriesId(indicatorId, countryCode) {
    var meta = indicatorMeta(indicatorId);
    var source = (meta && meta.source ? meta.source : "OECD").toLowerCase();
    return source + ":" + indicatorId + ":" + countryCode;
  }

  // 값 하나를 화면에 낼 모양으로. 관측 기간과 주기는 반드시 값과 함께 나간다.
  function decorate(indicatorId, countryCode, entry) {
    var indicator = indicatorMeta(indicatorId);
    var country = countryMeta(countryCode);
    return {
      id: seriesId(indicatorId, countryCode),
      indicator: indicatorId,
      topic: indicator ? indicator.topic : null,
      indicatorKo: indicator ? indicator.nameKo : indicatorId,
      indicatorEn: indicator ? indicator.nameEn : indicatorId,
      unitKo: indicator ? indicator.unitKo : "",
      headline: Boolean(indicator && indicator.headline),
      country: countryCode,
      countryKo: country.ko,
      countryEn: country.en,
      freq: indicator ? indicator.freq : null,
      freqKo: freqLabel(indicator ? indicator.freq : null),
      period: entry ? entry.period : null,
      periodKo: entry ? periodLabel(entry.period) : null,
      value: entry ? entry.value : null,
      loaded: Boolean(entry),
    };
  }

  // 주제 파일이 아직 안 왔으면 헤드라인 값이라도 쓴다(홈 카드 경로).
  function entryFor(indicatorId, countryCode) {
    var id = seriesId(indicatorId, countryCode);
    var indicator = indicatorMeta(indicatorId);
    var bucket = indicator && topics[indicator.topic];
    if (bucket && bucket.status === "ready" && bucket.series[id]) return bucket.series[id];
    var headline = (state.data && state.data.headlineSeries) || {};
    return headline[id] || null;
  }

  function find(indicatorId, countryCode) {
    var indicator = indicatorMeta(indicatorId);
    if (!indicator) return null;
    if (indicator.countries.indexOf(countryCode) < 0) return null;
    return decorate(indicatorId, countryCode, entryFor(indicatorId, countryCode));
  }

  // 전 계열 목록. 값은 주제 파일이 왔을 때만 채워진다(loaded 플래그로 구분).
  function all() {
    var rows = [];
    indicatorList().forEach(function (indicator) {
      indicator.countries.forEach(function (code) {
        rows.push(decorate(indicator.id, code, entryFor(indicator.id, code)));
      });
    });
    return rows;
  }

  // 지표명·국가명을 한글과 영문 양쪽에서 찾는다. index.json만으로 되므로
  // 기본 브라우즈 트리에 없는 지표도 걸린다.
  function search(query) {
    var needle = String(query || "").trim().toLowerCase();
    if (!needle) return [];
    var rows = [];
    indicatorList().forEach(function (indicator) {
      var indicatorHit = [indicator.nameKo, indicator.nameEn, indicator.id]
        .some(function (field) { return String(field).toLowerCase().includes(needle); });
      indicator.countries.forEach(function (code) {
        var country = countryMeta(code);
        var countryHit = [country.ko, country.en, code]
          .some(function (field) { return String(field).toLowerCase().includes(needle); });
        if (indicatorHit || countryHit) rows.push(decorate(indicator.id, code, entryFor(indicator.id, code)));
      });
    });
    return rows;
  }

  // 검색 결과를 그리려면 그 결과가 속한 주제 파일만 있으면 된다.
  function topicsOf(rows) {
    var ids = {};
    rows.forEach(function (row) { if (row.topic) ids[row.topic] = true; });
    return Object.keys(ids);
  }

  // 주제 1차 축: 주제 -> 지표 -> 국가.
  function byTopic(countryFilter) {
    return topicList().map(function (topic) {
      var indicators = indicatorList()
        .filter(function (indicator) { return indicator.topic === topic.id; })
        .map(function (indicator) {
          var codes = countryFilter ? indicator.countries.filter(function (c) { return c === countryFilter; }) : indicator.countries;
          return {
            id: indicator.id, nameKo: indicator.nameKo, nameEn: indicator.nameEn,
            unitKo: indicator.unitKo, freq: indicator.freq, freqKo: freqLabel(indicator.freq),
            headline: Boolean(indicator.headline), countryCount: codes.length,
            rows: codes.map(function (code) { return decorate(indicator.id, code, entryFor(indicator.id, code)); }),
          };
        })
        .filter(function (indicator) { return indicator.countryCount > 0; });
      return { id: topic.id, nameKo: topic.nameKo, nameEn: topic.nameEn, indicators: indicators };
    }).filter(function (topic) { return topic.indicators.length > 0; });
  }

  // 국가 우선 피벗: 한 나라의 전체 그림. 주제 순서를 유지한다.
  function byCountry(countryCode) {
    var order = topicList().map(function (topic) { return topic.id; });
    var groups = new Map();
    indicatorList().forEach(function (indicator) {
      if (indicator.countries.indexOf(countryCode) < 0) return;
      if (!groups.has(indicator.topic)) groups.set(indicator.topic, []);
      groups.get(indicator.topic).push(decorate(indicator.id, countryCode, entryFor(indicator.id, countryCode)));
    });
    return [...groups.entries()]
      .sort(function (a, b) { return order.indexOf(a[0]) - order.indexOf(b[0]); })
      .map(function (pair) {
        var topic = topicMeta(pair[0]);
        return { id: pair[0], nameKo: topic ? topic.nameKo : pair[0], rows: pair[1] };
      });
  }

  function formatValue(row) {
    if (!Number.isFinite(row.value)) return "–";
    var unit = row.unitKo || "";
    var digits = unit.indexOf("%") >= 0 ? 2 : unit.indexOf("지수") >= 0 ? 1 : 0;
    return row.value.toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  global.Indicators = {
    INDEX_SOURCES: INDEX_SOURCES,
    missingSources: missingSources,
    STALE_MONTHS: STALE_MONTHS,
    get state() { return state; },
    get topicCache() { return topics; },
    load: load,
    loadTopic: loadTopic,
    loadTopics: loadTopics,
    loadHistory: loadHistory,
    historyOf: historyOf,
    all: all,
    search: search,
    topicsOf: topicsOf,
    byTopic: byTopic,
    byCountry: byCountry,
    topicList: topicList,
    indicatorList: indicatorList,
    indicatorMeta: indicatorMeta,
    countryList: countryList,
    countryMeta: countryMeta,
    find: find,
    seriesId: seriesId,
    staleness: staleness,
    periodLabel: periodLabel,
    freqLabel: freqLabel,
    formatValue: formatValue,
    attribution: function () { return (state.data && state.data.attribution) || "OECD"; },
  };
})(window);
