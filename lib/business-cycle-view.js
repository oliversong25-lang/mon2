// lib/business-cycle-view.js
// 미국 4국면 모델 결과를 읽고 화면이 쓰기 좋은 모양으로 다듬는다. 홈 카드와 분석 탭이
// 같은 규칙(국면 이름, 증거 품질 표기, 신선도 판정)을 쓰도록 한 곳에 모았다 —
// 두 화면이 서로 다른 말로 같은 상태를 부르면 어느 쪽을 믿어야 할지 알 수 없다.
//
// ── 이 모델이 무엇이 아닌지부터 ────────────────────────────────────────────
// 이 모델은 **지금 미국 경제가 네 국면 중 어디인지**를 말한다. 침체를 예측하지 않고,
// 시점을 맞히지 않으며, 투자 판단을 내놓지 않는다. 상태는 `provisional`이고 최종
// 검증이 아니다. 그래서 이 파일은 국면 이름만 꺼내 주는 헬퍼가 아니라, 국면과 함께
// **증거 품질·회복 인식 지연·한계**를 반드시 같이 꺼내 주는 헬퍼다.
//
// ── 증거 품질이 낮다는 말의 뜻 ────────────────────────────────────────────
// 모델이 저장하는 `evidenceQuality`는 중립대·신선도·집중도·분리도를 모두 통과해야
// `high`가 된다. 낮다고 해서 국면이 틀렸다는 뜻이 아니고, 지금 증거로는 국면 사이
// 거리가 뚜렷하지 않다는 뜻이다. 이 구분을 화면에서 뭉개지 않는다.
(function (global) {
  "use strict";

  var URL = "data/business-cycle/us.json";

  // 월간 지표 위에 주간 판정을 얹으므로 발표 사이 주에는 값이 이월된다. 그건 정상이다.
  // 모델이 stale로 보는 기준은 8주이고, 화면도 같은 값을 쓴다(모델과 다른 기준으로
  // "낡았다"고 말하면 두 판정이 어긋난다).
  var STALE_WEEKS = 8;

  // 이 주 수를 넘도록 새 파일이 안 들어오면 배치 쪽을 의심해야 한다. 모델은 매주
  // 도는 것이 전제이므로, 3주를 넘기면 데이터가 아니라 파이프라인 문제다.
  var STALE_RUN_WEEKS = 3;

  var state = { status: "loading", data: null, error: null };

  function load() {
    return DataFetch.json(URL, { fresh: true })
      .then(function (payload) {
        if (!payload || !payload.current) {
          state = { status: "empty", data: null, error: null };
          return state;
        }
        state = { status: "ready", data: payload, error: null };
        return state;
      })
      .catch(function (error) {
        state = { status: "error", data: null, error: (error && error.message) || String(error) };
        return state;
      });
  }

  function phaseLabel(id) {
    var labels = (state.data && state.data.labels && state.data.labels.phases) || {};
    return labels[id] || id || "—";
  }

  function domainLabel(id) {
    var labels = (state.data && state.data.labels && state.data.labels.domains) || {};
    return labels[id] || id;
  }

  function statusLabel(id) {
    var labels = (state.data && state.data.labels && state.data.labels.status) || {};
    return labels[id] || id;
  }

  function alertLabel(id) {
    var labels = (state.data && state.data.labels && state.data.labels.alert) || {};
    return labels[id] || id;
  }

  // 국면마다 색을 하나씩 준다. 침체만 경고색이고 나머지는 중립 계열이다 —
  // 확장기를 "좋은 색"으로 칠하면 상태 서술이 아니라 평가가 된다.
  function phaseTone(id) {
    if (id === "contraction") return "bad";
    if (id === "slowdown") return "warn";
    return "neutral";
  }

  // 현재 국면 한 줄. 국면·증거 품질·기준일을 한 번에 꺼낸다. 셋 중 하나만 쓰는 화면을
  // 만들지 않기 위해 함께 반환한다.
  function current() {
    if (state.status !== "ready") return null;
    var now = state.data.current;
    return {
      asOf: now.asOf,
      phase: now.official,
      phaseKo: phaseLabel(now.official),
      tone: phaseTone(now.official),
      raw: now.raw,
      rawKo: phaseLabel(now.raw),
      status: now.status,
      statusKo: statusLabel(now.status),
      evidenceQuality: now.evidenceQuality,
      // 판정 보류 주에는 공식 국면이 없다. 빈칸으로 때우지 않고 그 사실을 그대로 넘긴다.
      withheld: now.status === "withheld",
      separation: now.separation,
      level: now.level,
      momentum: now.momentum,
      transitionWatch: now.transitionWatch,
      alert: now.recessionAlert || { level: "none", character: "absent" },
      breadth: now.breadth || {},
      concentration: now.concentration,
      domains: now.domains || {},
      freshness: now.freshness || {},
      // 원시 국면과 공식 국면이 다르면 확인 규칙이 아직 돌고 있다는 뜻이다.
      rawDiffers: Boolean(now.raw && now.official && now.raw !== now.official),
    };
  }

  // 도메인별 신선도. 모델이 쓰는 8주 기준을 그대로 쓰고, 넘은 것만 따로 모아 준다.
  function freshness() {
    if (state.status !== "ready") return null;
    var source = state.data.current.freshness || {};
    var rows = Object.keys(source).map(function (id) {
      var weeks = source[id] && source[id].weeks_since_release;
      return {
        id: id,
        label: domainLabel(id),
        weeks: typeof weeks === "number" ? weeks : null,
        arrived: Boolean(source[id] && source[id].new_observation_arrived_this_week),
        stale: typeof weeks === "number" && weeks >= STALE_WEEKS,
      };
    });
    return { rows: rows, stale: rows.filter(function (row) { return row.stale; }) };
  }

  // 배치가 멈췄는지. 관측이 오래된 것과 **모델을 안 돌린 것**은 다른 문제이므로 나눠 본다.
  function runFreshness() {
    if (state.status !== "ready") return null;
    var last = state.data.current.asOf;
    var weeks = Math.floor((Date.now() - Date.parse(last + "T00:00:00Z")) / (7 * 864e5));
    return { asOf: last, weeksSinceRun: weeks, stale: weeks > STALE_RUN_WEEKS };
  }

  // 최근 n주 경로. 분석 탭의 타임라인이 쓴다.
  function recent(weeks) {
    if (state.status !== "ready") return [];
    var rows = state.data.history || [];
    return weeks && weeks < rows.length ? rows.slice(rows.length - weeks) : rows.slice();
  }

  // 지금 국면이 몇 주째인지. 공식 국면이 바뀐 마지막 지점부터 센다.
  function currentRun() {
    if (state.status !== "ready") return null;
    var rows = state.data.history || [];
    var phase = state.data.current.official;
    var run = 0;
    for (var i = rows.length - 1; i >= 0; i -= 1) {
      if (rows[i].official !== phase) break;
      run += 1;
    }
    return { phase: phase, phaseKo: phaseLabel(phase), weeks: run, since: rows[rows.length - run] ? rows[rows.length - run].week : null };
  }

  function limitations() {
    return (state.status === "ready" && state.data.limitations) || [];
  }

  // 해석 경계. 평평한 한계 목록과 별개로 구조를 그대로 넘긴다.
  function interpretationBoundaries() {
    return (state.status === "ready" && state.data.interpretationBoundaries) || [];
  }

  // 국면 판독 옆에 붙어야 하는 경계. 화면이 목록을 훑어 고르지 않게 여기서 한 번만 고른다 —
  // 고르는 규칙이 화면마다 흩어지면 한쪽에서만 조용히 사라진다.
  function phaseReadingBoundary() {
    var rows = interpretationBoundaries();
    for (var i = 0; i < rows.length; i += 1) {
      if (rows[i] && rows[i].surface === "app_phase_reading") return rows[i];
    }
    return null;
  }

  // 폭이 집중도의 부분적 화면이라는 설명. 자동 분류가 아니라 주의 표시라서
  // 모델이 쓴 문장을 그대로 넘기고 화면에서 계산하지 않는다.
  function concentrationScreen() {
    if (state.status !== "ready") return null;
    var breadth = (state.data.current && state.data.current.breadth) || null;
    return (breadth && breadth.partial_concentration_screen) || null;
  }

  function recoveryWarning() {
    return (state.status === "ready" && state.data.recoveryLatencyWarning) || null;
  }

  function summary() {
    return (state.status === "ready" && state.data.summary) || null;
  }

  function provenance() {
    return (state.status === "ready" && state.data.provenance) || null;
  }

  global.BusinessCycle = {
    URL: URL,
    STALE_WEEKS: STALE_WEEKS,
    load: load,
    get state() { return state; },
    current: current,
    currentRun: currentRun,
    freshness: freshness,
    runFreshness: runFreshness,
    recent: recent,
    limitations: limitations,
    interpretationBoundaries: interpretationBoundaries,
    phaseReadingBoundary: phaseReadingBoundary,
    concentrationScreen: concentrationScreen,
    recoveryWarning: recoveryWarning,
    summary: summary,
    provenance: provenance,
    phaseLabel: phaseLabel,
    domainLabel: domainLabel,
    statusLabel: statusLabel,
    alertLabel: alertLabel,
    phaseTone: phaseTone,
  };
})(window);
