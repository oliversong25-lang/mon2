// lib/portfolio.js
// 자산 목록을 홈 화면이 쓰는 집계로 바꾼다. 평가금액 자체는 계산하지 않고
// lib/valuation.js에 위임한다 — 금액 계산은 단일 출처여야 한다.
//
// 분류표(원금 보장 / 현금화 용이성 / 성장 가능성)는 우리가 작성한 판단이다.
// 데이터에서 유도되는 값이 아니므로 화면에 기준을 밝히고, 여기 한곳에만 둔다.
(function (global) {
  "use strict";

  var GROUP_NAMES = {
    cash: "현금",
    savings: "예금·적금",
    equity: "주식·ETF",
    crypto: "가상자산",
    fund: "펀드",
    bond: "채권",
    commodity: "원자재·실물자산",
    realestate: "부동산",
  };

  // 원금 보장 — "안전/위험"이 아니다. 금을 안전자산이라 부르는 관행이 있으나 금은
  // 변동성이 크고 이자가 없어, "안전"이라는 말이 원금 보전을 연상시키는 오해를 만든다.
  // 외화 현금은 환율에 따라 원화 가치가 변하므로 시가 변동 쪽이다.
  function principalGuaranteed(asset) {
    if (asset.group === "savings") return true;
    if (asset.group === "cash") return (asset.fields || {}).currency === "KRW" || !(asset.fields || {}).currency;
    return false;
  }

  function currencyOf(asset) {
    if (asset.group === "cash") return (asset.fields || {}).currency || "KRW";
    return (asset.autoFields || {}).currency || "KRW";
  }

  // 현금화 용이성 — 즉시 / 중간 / 낮음
  function liquidity(asset) {
    if (["cash", "equity", "crypto"].indexOf(asset.group) >= 0) return "즉시";
    if (["fund", "bond", "commodity"].indexOf(asset.group) >= 0) return "중간";
    return "낮음"; // 만기 전 예적금, 부동산
  }

  // 성장 가능성 — 낮음 / 중간 / 높음.
  // 부동산은 지역에 따라 나누지 않고 "중간" 고정이다. 과거 상승률을 등급으로 바꾸는
  // 순간 그건 전망이 되고, 화면에 "당신 집이 있는 지역은 성장 가능성 낮음"이 뜨는 것은
  // 사실상 지역 평가이자 매도 판단에 가깝다. 지역 데이터는 상세 화면에서 사실로만 준다.
  function growth(asset) {
    var group = asset.group;
    var fields = asset.fields || {};
    var auto = asset.autoFields || {};
    if (group === "cash" || group === "savings") return "낮음";
    if (group === "bond") {
      var bondType = auto.bondType || fields.bondTypeManual || "";
      return bondType === "회사채" ? "중간" : "낮음";
    }
    if (group === "fund") {
      var fundType = auto.fundType || fields.fundTypeManual || "";
      if (fundType === "채권형") return "낮음";
      if (fundType === "주식형") return "높음";
      return "중간";
    }
    if (group === "commodity") {
      var kind = fields.assetKind || "";
      return kind === "원유/에너지" ? "높음" : "중간";
    }
    if (group === "realestate") return "중간";
    return "높음"; // 주식·ETF·가상자산
  }

  var GROWTH_SCORE = { 낮음: 0, 중간: 0.5, 높음: 1 };

  function currencyLabel(code) {
    return { KRW: "원화", USD: "달러", JPY: "엔", EUR: "유로", CNY: "위안" }[code] || code;
  }

  function assetName(asset) {
    var fields = asset.fields || {};
    return (
      fields.productName ||
      fields.directName ||
      fields.nickname ||
      (asset.group === "cash" ? (fields.currency || "KRW") + " 현금" : "") ||
      (asset.group === "savings" ? (fields.productType || "예금") + (fields.institution ? " · " + fields.institution : "") : "") ||
      (asset.group === "realestate" ? fields.propertyType || "부동산" : "") ||
      (asset.group === "commodity" ? fields.assetKind || "원자재" : "") ||
      GROUP_NAMES[asset.group] ||
      "자산"
    );
  }

  // 자산 목록 -> 홈 화면이 필요한 모든 집계.
  function summarize(assets) {
    var rows = [];
    var unavailable = [];
    var total = 0;

    assets.forEach(function (asset) {
      var valued = Valuation.valuate(asset);
      if (valued.unavailable) {
        unavailable.push({ asset: asset, name: assetName(asset), reason: valued.reason });
        return;
      }
      var bought = Valuation.purchase(asset);
      total += valued.krw;
      rows.push({
        asset: asset,
        id: asset.id,
        group: asset.group,
        groupName: GROUP_NAMES[asset.group] || asset.group,
        name: assetName(asset),
        krw: valued.krw,
        purchaseKrw: bought ? bought.krw : null,
        profit: bought ? valued.krw - bought.krw : null,
        guaranteed: principalGuaranteed(asset),
        currency: currencyOf(asset),
        liquidity: liquidity(asset),
        growth: growth(asset),
      });
    });

    rows.sort(function (a, b) {
      return b.krw - a.krw;
    });

    // 손익의 모집단은 총자산의 모집단보다 작다 — 화면에서 반드시 구분해 보여준다.
    var withPurchase = rows.filter(function (row) {
      return row.purchaseKrw !== null && row.purchaseKrw > 0;
    });
    var profitSum = withPurchase.reduce(function (sum, row) {
      return sum + row.profit;
    }, 0);
    var purchaseSum = withPurchase.reduce(function (sum, row) {
      return sum + row.purchaseKrw;
    }, 0);

    return {
      rows: rows,
      total: total,
      unavailable: unavailable,
      noPurchaseCount: rows.length - withPurchase.length,
      profit: {
        count: withPurchase.length,
        sum: profitSum,
        purchaseSum: purchaseSum,
        rate: purchaseSum ? profitSum / purchaseSum : null,
      },
      axes: {
        group: bucket(rows, total, function (row) {
          return row.groupName;
        }),
        guaranteed: bucket(rows, total, function (row) {
          return row.guaranteed ? "원금 보장" : "시가 변동";
        }),
        currency: bucket(rows, total, function (row) {
          return currencyLabel(row.currency);
        }),
        growth: bucket(rows, total, function (row) {
          return row.growth;
        }),
      },
      meters: meters(rows, total),
    };
  }

  function bucket(rows, total, keyOf) {
    var map = new Map();
    rows.forEach(function (row) {
      var key = keyOf(row);
      map.set(key, (map.get(key) || 0) + row.krw);
    });
    return [...map.entries()]
      .map(function (entry) {
        return { label: entry[0], krw: entry[1], share: total ? entry[1] / total : 0 };
      })
      .sort(function (a, b) {
        return b.krw - a.krw;
      });
  }

  // 축은 "위치"를 나타낸다. 점수도 등급도 아니다 — 0에서 100으로 차오르는 막대나
  // 상·중·하 배지를 쓰지 않는 이유가 이것이다. 집중도가 높은 것은 나쁜 것이 아니다.
  function meters(rows, total) {
    if (!total) return [];
    var guaranteed = rows.reduce(function (sum, row) {
      return sum + (row.guaranteed ? row.krw : 0);
    }, 0);
    var instant = rows.reduce(function (sum, row) {
      return sum + (row.liquidity === "즉시" ? row.krw : 0);
    }, 0);
    var top3 = rows.slice(0, 3).reduce(function (sum, row) {
      return sum + row.krw;
    }, 0);
    var growthWeighted = rows.reduce(function (sum, row) {
      return sum + GROWTH_SCORE[row.growth] * row.krw;
    }, 0);
    return [
      { key: "guaranteed", label: "원금 보장 비중", value: guaranteed / total, note: "원화 현금·예적금 ÷ 총자산" },
      { key: "liquidity", label: "현금화 용이성", value: instant / total, note: "즉시 현금화 가능 자산 ÷ 총자산" },
      { key: "concentration", label: "집중도", value: top3 / total, note: "상위 3개 자산 비중 합" },
      { key: "growth", label: "성장 가능성", value: growthWeighted / total, note: "자산별 등급의 금액 가중 평균" },
    ];
  }

  global.Portfolio = {
    GROUP_NAMES: GROUP_NAMES,
    summarize: summarize,
    assetName: assetName,
    principalGuaranteed: principalGuaranteed,
    liquidity: liquidity,
    growth: growth,
    currencyLabel: currencyLabel,
  };
})(window);
