// 의사결정 기록의 **자동 채움**. 사용자가 타자로 쳐야 하는 것을 근거 네 칸과 반증 조건
// 하나로 줄이는 것이 이 파일의 전부다.
//
// 마찰이 의사결정 기록이 실패하는 유일한 이유다. 한 건에 10분이 걸리면 아무도 쓰지 않고,
// 안 쓰면 나중에 되돌아볼 기록이 없다. 그래서 날짜·종목·수량·단가·자산 내 비중·환율·
// 주가지수·금리·그때의 경기국면은 **전부 앱이 채운다.**
//
// ── 경기국면은 맥락이지 신호가 아니다 ──────────────────────────────────────
// 트랙 17·19·23~26이 순환매·가치 타이밍·시장 수익·시장 분산·듀레이션 축을 모두 검정했고
// 전부 부정이었다. 검증이 지지한 유일한 용도가 **서술과 상태 인식**이다. 그래서 여기서도
// 국면은 "그때 무슨 국면이었나"를 남기는 것이고, 무엇을 하라는 신호가 아니다.
(function (global) {
  "use strict";

  var QUOTES_URL = "data/quotes.json";
  var RATES_URL = "data/indicators/latest/rates.json";
  var EQUITY_URL = "data/indicators/oecd/DF_FINMARK.json";

  // 한국 주식시장 수준. **이것은 KOSPI 종가가 아니다.** 이 저장소에 KOSPI 지수 피드가
  // 없어서, 있는 것 중 가장 가까운 OECD 월간 주가지수(한국)를 그 이름 그대로 담는다.
  // "코스피"라고 적으면 없는 정확도를 만들어내는 셈이므로 라벨을 정확히 쓴다.
  var EQUITY_SERIES = "oecd:share-prices:KOR";
  var EQUITY_LABEL = "OECD 주가지수(한국·월간)";

  // 기록에 남길 금리. 전부 담으면 읽을 수 없고, 하나만 담으면 나중에 다른 것이 필요해진다.
  // 단기·장기 하나씩이면 "그때 금리가 어땠나"에 답할 수 있다.
  var RATE_KEYS = [
    { key: "ustreasury:us-tb-3m:USA", id: "us3m", label: "미 국채 3개월" },
    { key: "ustreasury:us-tb-10y:USA", id: "us10y", label: "미 국채 10년" },
  ];

  var cache = {};

  async function fetchJson(url) {
    if (cache[url]) return cache[url];
    var response = await fetch(url, { cache: "no-cache" });
    if (!response.ok) throw new Error(url + " 응답 " + response.status);
    cache[url] = await response.json();
    return cache[url];
  }

  // 실패해도 기록 자체는 남아야 한다. 맥락 한 조각이 없다고 결정 근거를 못 쓰게 만들면
  // 본말이 뒤집힌다 — 없는 값은 null로 두고 왜 없는지를 함께 적는다.
  async function soft(url, read) {
    try { return read(await fetchJson(url)); }
    catch (error) { console.error("[결정 기록] 맥락 수집 실패:", url, error); return null; }
  }

  async function marketContext() {
    var fx = await soft(QUOTES_URL, function (quotes) {
      var rates = quotes.rates || {};
      return { usdkrw: rates.USD || null, asOf: quotes.asOf || null };
    });
    var rates = await soft(RATES_URL, function (payload) {
      var series = payload.series || {};
      var out = {};
      RATE_KEYS.forEach(function (entry) {
        var row = series[entry.key];
        out[entry.id] = row ? { label: entry.label, value: row.value, period: row.period } : null;
      });
      return out;
    });
    var equity = await soft(EQUITY_URL, function (payload) {
      var series = (payload.series && Object.values(payload.series)) || [];
      var row = series.find(function (item) { return item.id === EQUITY_SERIES; });
      var last = row && row.observations && row.observations[row.observations.length - 1];
      if (!last) return null;
      return { label: EQUITY_LABEL, period: last[0], value: last[1], note: "KOSPI 종가가 아니라 월간 지수다." };
    });
    return { fx: fx, rates: rates, koreanEquityIndex: equity };
  }

  // 경기국면. `BusinessCycle`이 이미 로드돼 있으면 그것을 쓰고, 아니면 파일을 직접 읽는다.
  async function phaseContext() {
    if (global.BusinessCycle && BusinessCycle.state && BusinessCycle.state.status === "ready") {
      var data = BusinessCycle.state.data;
      return {
        phase: data.current.official,
        phaseKo: BusinessCycle.phaseLabel(data.current.official),
        asOf: data.current.asOf,
        evidenceQuality: data.current.evidenceQuality,
        variant: data.variant ? data.variant.id : null,
        recordedAs: "context_not_signal",
      };
    }
    return await soft(global.BusinessCycle ? BusinessCycle.URL : "data/business-cycle/us.json", function (data) {
      return {
        phase: data.current.official,
        phaseKo: (data.labels && data.labels.phases && data.labels.phases[data.current.official]) || data.current.official,
        asOf: data.current.asOf,
        evidenceQuality: data.current.evidenceQuality,
        variant: data.variant ? data.variant.id : null,
        recordedAs: "context_not_signal",
      };
    });
  }

  // 보유 자산 한 건의 그때 값. `Portfolio.summarize`가 이미 계산하는 것을 다시 계산하지
  // 않는다 — 두 곳에서 세면 언젠가 갈라진다.
  function holdingContext(row, total) {
    if (!row) return null;
    return {
      id: row.id,
      name: row.name,
      group: row.group,
      groupName: row.groupName,
      quantity: row.quantity,
      unitLabel: row.unitLabel,
      unitPrice: row.unitPrice,
      averagePrice: row.averagePrice,
      currency: row.currency,
      valueKrw: row.krw,
      shareOfTotal: total > 0 ? Number((row.krw / total).toFixed(6)) : null,
    };
  }

  // 기록 한 건의 맥락 전체. 사용자는 이 중 어느 것도 입력하지 않는다.
  async function build(options) {
    options = options || {};
    var market = await marketContext();
    var phase = await phaseContext();
    return {
      capturedAt: new Date().toISOString(),
      holding: holdingContext(options.holding, options.totalKrw || 0),
      totalAssetsKrw: options.totalKrw || null,
      market: market,
      businessCycle: phase,
      // 어느 화면이 채웠는지. 나중에 자동 채움이 바뀌면 옛 기록을 어떻게 읽어야 하는지
      // 알아야 한다.
      filledBy: "decision-context@1",
    };
  }

  // 사용자가 실제로 타자로 쳐야 하는 칸. 시험이 이 수를 고정한다 — 마찰이 늘면 기록이
  // 안 쌓이고, 늘어난 것을 눈으로 세는 대신 여기서 세게 한다.
  var TYPED_FIELDS = ["reasoning", "expectation", "uncertainty", "falsificationText"];

  // 앱이 채우는 칸. 사용자가 손대지 않는다.
  var AUTOFILLED_FIELDS = [
    "decidedAt", "holding.name", "holding.quantity", "holding.unitPrice",
    "holding.valueKrw", "holding.shareOfTotal", "market.fx.usdkrw",
    "market.rates.us3m", "market.rates.us10y", "market.koreanEquityIndex",
    "businessCycle.phase",
  ];

  global.DecisionContext = {
    build: build,
    holdingContext: holdingContext,
    marketContext: marketContext,
    phaseContext: phaseContext,
    TYPED_FIELDS: TYPED_FIELDS,
    AUTOFILLED_FIELDS: AUTOFILLED_FIELDS,
  };
})(window);
