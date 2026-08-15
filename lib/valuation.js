// lib/valuation.js
// 자산 평가금액의 단일 출처. asset-input.html과 home.html이 함께 쓴다.
//
// 왜 공통 모듈인가: 평가 로직이 두 파일에 각각 있으면 한쪽만 고쳐지는 순간 같은 자산이
// 화면마다 다른 금액으로 보인다. 총자산은 사용자가 이 앱에서 가장 먼저 보는 숫자라
// 그런 불일치는 곧바로 신뢰 문제가 된다.
//
// ES 모듈이 아니라 전역(window.Valuation)으로 노출하는 이유: asset-input.html의 스크립트가
// 클래식 스크립트이고 session·render 같은 전역에 의존한다(회귀 테스트도 page.evaluate로
// 그 전역을 직접 읽는다). 모듈로 바꾸면 스코프가 닫혀 그 경로가 전부 깨진다.
(function (global) {
  "use strict";

  var GRAMS_PER_DON = 3.75;

  // 배치가 구운 data/quotes.json의 런타임 상태. 하드코딩된 시세를 여기에도, 호출부에도
  // 두지 않는다 — 값이 없으면 "시세 확인 불가"이지 그럴듯한 기본값이 아니다.
  var state = {
    status: "loading", // loading | ready | error
    asOf: null,
    sources: {},
    quotes: {},   // 국내 주식·ETF·ETN, 6자리 종목코드 기준
    crypto: {},   // 가상자산, 심볼 기준 (BTC/ETH/...)
    // 가상자산만 날짜가 다를 수 있다. 코인게코가 실패한 날 배치는 국내 시세만 갱신하고
    // 직전 코인 값을 그대로 들고 가므로, 그 값이 언제 것인지 별도로 들고 있어야
    // 화면이 "며칠 전 값"이라고 말할 수 있다. 날짜가 섞인 데이터를 한 시점의
    // 스냅샷인 것처럼 보여주면 안 된다.
    cryptoAsOf: null,
    // 전일 종가가 가리키는 날짜. 배치가 확정하지 못하면 null이고, 화면은 그때
    // 날짜를 지어내지 않고 "직전 거래일"이라고만 적는다.
    prevCloseDate: null,
    rates: { KRW: 1 },
    // 전일 환율과 그 날짜. 없으면 외화 자산이 변화 계산에서 빠진다.
    prevRates: null,
    prevRatesDate: null,
    // 가상자산의 비교 기준은 거래소 전일 종가가 아니라 24시간 전이다.
    cryptoPrevAt: null,
    commodities: {},
  };

  var QUOTES_PATH = "data/quotes.json";

  function load() {
    // 매일 바뀌는 파일이라 캐시를 그대로 믿으면 어제 값을 들고 있게 된다. 실측으로
    // 그냥 요청하면 asOf 2026-08-12, 캐시를 비껴가면 2026-08-13이 온 적이 있고
    // 화면에는 배치가 고장 난 것과 똑같이 보였다. DataFetch가 조건부 요청으로
    // 매번 서버에 확인한다(내용이 그대로면 304라 본문은 다시 받지 않는다).
    return DataFetch.json(QUOTES_PATH, { fresh: true })
      .then(function (payload) {
        state.asOf = payload.asOf || null;
        state.sources = payload.sources || {};
        state.quotes = payload.quotes || {};
        state.crypto = payload.crypto || {};
        state.cryptoAsOf = payload.cryptoAsOf || null;
        state.prevCloseDate = payload.prevCloseDate || null;
        state.prevRates = payload.prevRates || null;
        state.prevRatesDate = payload.prevRatesDate || null;
        state.cryptoPrevAt = payload.cryptoPrevAt || null;
        state.rates = Object.assign({ KRW: 1 }, payload.rates || {});
        state.commodities = payload.commodities || {};
        state.status = "ready";
        return state;
      })
      .catch(function (error) {
        console.error("[시세 데이터] quotes.json 로드 실패:", error);
        state.status = "error";
        return state;
      });
  }

  // 이 값이 방금 받은 것인지, 브라우저가 들고 있던 것인지. 화면이 그 둘을 구별해
  // 말할 수 있어야 "배치가 멈춘 것"과 "내 캐시가 낡은 것"이 섞이지 않는다.
  function loadInfo() {
    return global.DataFetch ? DataFetch.loadInfo(QUOTES_PATH) : null;
  }

  function rate(currency) {
    return state.rates[currency || "KRW"] || (currency === "KRW" ? 1 : 0);
  }

  // 환율을 모르는 통화를 1:1로 치면 외화 자산이 원화로 둔갑한다. 0을 돌려주는 대신
  // null을 돌려 호출부가 "확인 불가"로 처리하게 한다.
  function toKrw(value, currency) {
    var amount = Number(value || 0);
    if (!amount) return 0;
    if (!currency || currency === "KRW") return Math.round(amount);
    var fx = state.rates[currency];
    if (!fx) return null;
    return Math.round(amount * fx);
  }

  function assetCurrency(asset) {
    var auto = asset.autoFields || {};
    var fields = asset.fields || {};
    if (asset.group === "cash") return fields.currency || "KRW";
    return auto.currency || "KRW";
  }

  // 종목·코인·귀금속의 단가. 자산이 표시되는 통화 기준으로 돌려준다.
  function priceFor(group, code, fields) {
    if (group === "equity") {
      var quote = state.quotes[code];
      return quote && quote.price > 0 ? quote.price : null;
    }
    if (group === "crypto") {
      var coin = state.crypto[String(code || "").toUpperCase()];
      return coin && coin.price > 0 ? coin.price : null;
    }
    if (group === "commodity") {
      var kind = (fields || {}).assetKind;
      var perGram = kind === "은" ? state.commodities.silverPerGram : state.commodities.goldPerGram;
      return perGram > 0 ? perGram : null;
    }
    return null;
  }

  // 원자재 수량 단위는 자산 종류·보유 방식에 따라 g 또는 돈이다(금 실물만 돈).
  function gramsOf(fields) {
    var quantity = Number((fields || {}).quantity || 0);
    if (!quantity) return 0;
    var isDon = fields.holdingMethod === "실물 보유" && fields.assetKind === "금";
    return isDon ? quantity * GRAMS_PER_DON : quantity;
  }

  // 자산 1건의 평가금액.
  // { krw, value, currency, unavailable, reason }
  //   unavailable=true면 총자산 합계에서 빼고 화면에 건수를 밝힌다 — 조용히 0으로 더하지 않는다.
  function valuate(asset) {
    var group = asset.group;
    var fields = asset.fields || {};
    var currency = assetCurrency(asset);
    var unavailable = function (reason) {
      return { krw: null, value: null, currency: currency, unavailable: true, reason: reason };
    };
    var ok = function (value, cur) {
      var useCurrency = cur || currency;
      var krw = toKrw(value, useCurrency);
      if (krw === null) return unavailable("환율 없음(" + useCurrency + ")");
      return { krw: krw, value: value, currency: useCurrency, unavailable: false, reason: null };
    };

    if (group === "cash") return ok(Number(fields.amount || 0), fields.currency || "KRW");
    if (group === "savings") return ok(Number(fields.balance || 0), "KRW");
    if (group === "fund" || group === "bond") return ok(Number(fields.valuation || 0), "KRW");

    if (group === "equity" || group === "crypto") {
      var quantity = Number(fields.quantity || 0);
      if (!quantity) return ok(0);
      var price = priceFor(group, fields.productCode, fields);
      if (!price) return unavailable("시세 없음");
      return ok(quantity * price);
    }

    if (group === "commodity") {
      if (fields.holdingMethod === "기타") return ok(Number(fields.valuation || 0), "KRW");
      var grams = gramsOf(fields);
      if (!grams) return ok(0, "KRW");
      var perGram = priceFor("commodity", null, fields);
      if (!perGram) return unavailable("시세 없음");
      return ok(grams * perGram, "KRW");
    }

    if (group === "realestate") {
      var value = Number(fields.valuation || 0);
      // 공동명의 지분율을 빠뜨리면 총자산이 그대로 부풀어 오른다.
      var joint = fields.joint === true || fields.joint === "true";
      var share = joint ? Number(fields.ownershipRate || 0) / 100 : 1;
      if (joint && !(share > 0)) return unavailable("지분율 미입력");
      return ok(value * share, "KRW");
    }

    return ok(0);
  }

  // ── 전일 종가 기준 평가 ────────────────────────────────────────────────
  //
  // 총자산 변화를 "직전에 이 브라우저가 본 값"과 비교하면 두 가지가 섞인다. 며칠 만에
  // 열면 며칠치 변화가 나오고, 자산을 하나 추가하면 그 금액이 통째로 변동으로 잡힌다.
  // 그래서 기준선을 방문 이력이 아니라 시세 데이터에서 뽑는다 — **지금 들고 있는 자산을
  // 전일 종가로 다시 평가**한다. 그러면 자산을 더하거나 빼도 양쪽이 같이 움직여
  // 변화로 잡히지 않고, 남는 것은 가격 움직임뿐이다.
  //
  // 전일 값이 없는 자산은 0으로 때우지 않고 뺀다. 0으로 채우면 화면에 "변동 없음"으로
  // 보이는데, 그건 모르는 것과 다르다.
  function previousPriceFor(group, code, fields) {
    if (group === "equity") {
      var quote = state.quotes[code];
      return quote && quote.prevClose > 0 ? quote.prevClose : null;
    }
    if (group === "crypto") {
      // 코인게코는 전일 종가가 아니라 24시간 변동률을 준다. 배치가 그걸로 24시간 전
      // 가격을 되돌려 실어 준다(기준 시점이 국내 종가와 다르다는 것은 따로 밝힌다).
      var coin = state.crypto[String(code || "").toUpperCase()];
      return coin && coin.prev > 0 ? coin.prev : null;
    }
    if (group === "commodity") {
      var kind = (fields || {}).assetKind;
      // 은은 ECOS에서 오고 전일 값을 싣지 않는다. 금만 전일 종가가 있다.
      if (kind === "은") return null;
      return state.commodities.goldPerGramPrev > 0 ? state.commodities.goldPerGramPrev : null;
    }
    return null;
  }

  // 전일 환율로 원화 환산. 모르면 null — 1:1로 치면 외화가 원화로 둔갑한다.
  function toKrwPrevious(value, currency) {
    var amount = Number(value || 0);
    if (!amount) return 0;
    if (!currency || currency === "KRW") return Math.round(amount);
    var fx = state.prevRates && state.prevRates[currency];
    if (!fx) return null;
    return Math.round(amount * fx);
  }

  // 자산 1건을 전일 종가로 평가한다. 모양은 valuate()와 같다.
  function valuatePrevious(asset) {
    var group = asset.group;
    var fields = asset.fields || {};
    var currency = assetCurrency(asset);
    var unavailable = function (reason) {
      return { krw: null, unavailable: true, reason: reason };
    };

    // 현금은 수량이 그대로이고 환율만 움직인다 — 전일 환율로 되돌린다.
    if (group === "cash") {
      var cashKrw = toKrwPrevious(Number(fields.amount || 0), currency);
      return cashKrw === null ? unavailable("전일 환율 없음(" + currency + ")") : { krw: cashKrw, unavailable: false, reason: null };
    }

    // 시세와 무관한 원화 자산은 전일에도 같은 금액이다 — 가격이 움직인 게 아니라 그대로다.
    if (group === "savings" || group === "fund" || group === "bond" || group === "realestate") {
      if (currency !== "KRW") return unavailable("전일 환율 없음(" + currency + ")");
      var current = valuate(asset);
      return current.unavailable ? unavailable(current.reason) : { krw: current.krw, unavailable: false, reason: null };
    }

    if (group === "equity" || group === "crypto") {
      var quantity = Number(fields.quantity || 0);
      if (!quantity) return { krw: 0, unavailable: false, reason: null };
      var price = previousPriceFor(group, fields.productCode, fields);
      if (!price) return unavailable(group === "crypto" ? "24시간 전 값 없음" : "전일 종가 없음");
      var prevKrw = toKrwPrevious(quantity * price, currency);
      return prevKrw === null
        ? unavailable("전일 환율 없음(" + currency + ")")
        : { krw: prevKrw, unavailable: false, reason: null };
    }

    if (group === "commodity") {
      if (fields.holdingMethod === "기타") return { krw: Math.round(Number(fields.valuation || 0)), unavailable: false, reason: null };
      var grams = gramsOf(fields);
      if (!grams) return { krw: 0, unavailable: false, reason: null };
      var perGram = previousPriceFor("commodity", null, fields);
      if (!perGram) return unavailable("전일 종가 없음");
      return { krw: Math.round(grams * perGram), unavailable: false, reason: null };
    }

    return unavailable("전일 값 없음");
  }

  function prevCloseDate() {
    return state.prevCloseDate || null;
  }

  // 매입금액. 없으면 null — 손익 계산의 모집단은 총자산의 모집단보다 작다.
  // 현금·예적금은 대상이 아니다: 예적금 잔액에는 이자가 이미 포함돼 있을 수 있어
  // 연이율을 곱하면 이중 계산이 된다.
  function purchase(asset) {
    var group = asset.group;
    var fields = asset.fields || {};
    if (group === "cash" || group === "savings") return null;

    if (group === "equity" || group === "crypto") {
      var quantity = Number(fields.quantity || 0);
      var average = Number(fields.averagePrice || 0);
      if (!quantity || !average) return null;
      var currency = assetCurrency(asset);
      var krw = toKrw(quantity * average, currency);
      return krw === null ? null : { krw: krw, currency: currency };
    }

    if (group === "realestate") {
      var price = Number(fields.purchasePrice || 0);
      if (!price) return null;
      var joint = fields.joint === true || fields.joint === "true";
      var share = joint ? Number(fields.ownershipRate || 0) / 100 : 1;
      if (joint && !(share > 0)) return null;
      return { krw: Math.round(price * share), currency: "KRW" };
    }

    var amount = Number(fields.purchaseAmount || 0);
    return amount ? { krw: Math.round(amount), currency: "KRW" } : null;
  }

  function asOfLabel() {
    if (!state.asOf) return "";
    var parsed = new Date(state.asOf);
    if (isNaN(parsed.getTime())) return "";
    return parsed.getMonth() + 1 + "월 " + parsed.getDate() + "일 종가 기준";
  }

  function asOfDate() {
    return state.asOf ? String(state.asOf).slice(0, 10) : null;
  }

  // 가상자산 기준일이 나머지보다 이르면 그 날짜를 돌려준다(아니면 null).
  // 호출부는 이 값이 있을 때만 "며칠 전 값" 안내를 띄운다.
  function staleCryptoDate() {
    var main = asOfDate();
    if (!state.cryptoAsOf || !main) return null;
    return state.cryptoAsOf < main ? state.cryptoAsOf : null;
  }

  global.Valuation = {
    state: state,
    load: load,
    rate: rate,
    toKrw: toKrw,
    priceFor: priceFor,
    gramsOf: gramsOf,
    valuate: valuate,
    valuatePrevious: valuatePrevious,
    previousPriceFor: previousPriceFor,
    prevCloseDate: prevCloseDate,
    prevRatesDate: function () { return state.prevRatesDate || null; },
    cryptoPrevAt: function () { return state.cryptoPrevAt || null; },
    purchase: purchase,
    asOfLabel: asOfLabel,
    asOfDate: asOfDate,
    loadInfo: loadInfo,
    staleCryptoDate: staleCryptoDate,
    GRAMS_PER_DON: GRAMS_PER_DON,
  };
})(window);
