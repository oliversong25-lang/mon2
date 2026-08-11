// lib/portfolio.js
// 자산 목록을 홈 화면이 쓰는 집계로 바꾼다. 평가금액 자체는 계산하지 않고
// lib/valuation.js에 위임한다 — 금액 계산은 단일 출처여야 한다.
//
// 분류표(원금 보장 / 현금화 용이성 / 성장 가능성)는 우리가 작성한 판단이다.
// 데이터에서 유도되는 값이 아니므로 화면에 기준을 밝히고, 여기 한곳에만 둔다.
(function (global) {
  "use strict";

  // 금융자산과 실물자산을 나눈다. 부동산 하나가 90%를 넘는 포트폴리오에서는 한 그래프에
  // 다 넣으면 나머지 자산군이 전부 실 가닥으로 뭉쳐 읽히지 않는다. 그리고 원금 보장·
  // 현금화·집중도는 투자 판단과 연결된 지표인데, 실거주 아파트를 팔아 집중도를 낮출 수는
  // 없다 — 부동산은 그 판단의 대상이 아니므로 요약 축에서도 뺀다.
  var ASSET_CLASS = {
    cash: "financial",
    savings: "financial",
    equity: "financial",
    fund: "financial",
    bond: "financial",
    crypto: "financial",
    commodity: "physical",
    realestate: "physical",
  };

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
        klass: ASSET_CLASS[asset.group] || "financial",
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

    // 금융자산과 실물자산의 손익은 성격이 다르다. 주식 손익은 시장이 매긴 값이고,
    // 부동산의 차액은 사용자가 적어 넣은 추정가와 매입가의 차이다. 한 줄에 더하면
    // 숫자의 성격이 섞이고, 실제로 그렇게 더한 값은 98.7%가 부동산이었다.
    // 그래서 금융자산은 '평가손익', 실물자산은 '평가차액'으로 따로 낸다.
    function profitOf(subset) {
      var scoped = subset.filter(function (row) {
        return row.purchaseKrw !== null && row.purchaseKrw > 0;
      });
      var sum = scoped.reduce(function (acc, row) {
        return acc + row.profit;
      }, 0);
      var basis = scoped.reduce(function (acc, row) {
        return acc + row.purchaseKrw;
      }, 0);
      return { count: scoped.length, sum: sum, purchaseSum: basis, rate: basis ? sum / basis : null };
    }

    var financialRows = rows.filter(function (row) {
      return row.klass === "financial";
    });
    var physicalRows = rows.filter(function (row) {
      return row.klass === "physical";
    });
    var financialTotal = sumKrw(financialRows);
    var physicalTotal = sumKrw(physicalRows);

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
      financial: {
        rows: financialRows,
        total: financialTotal,
        share: total ? financialTotal / total : 0,
        profit: profitOf(financialRows),
        axes: {
          group: bucket(financialRows, financialTotal, function (row) {
            return row.groupName;
          }),
          guaranteed: bucket(financialRows, financialTotal, function (row) {
            return row.guaranteed ? "원금 보장" : "시가 변동";
          }),
          currency: bucket(financialRows, financialTotal, function (row) {
            return currencyLabel(row.currency);
          }),
          growth: bucket(financialRows, financialTotal, function (row) {
            return row.growth;
          }),
        },
      },
      physical: {
        rows: physicalRows,
        total: physicalTotal,
        share: total ? physicalTotal / total : 0,
        // 시장이 준 손익이 아니라 사용자가 입력한 추정가와 매입가의 차이다.
        gap: profitOf(physicalRows),
        // 실물자산은 전부 시가 변동이고 전부 원화라 원금 보장·통화 축이 의미가 없다.
        axes: {
          group: bucket(physicalRows, physicalTotal, function (row) {
            return row.groupName;
          }),
        },
      },
      // 요약 축은 금융자산 기준으로 고정한다. 구성 카드의 전환과 연동하지 않는다.
      meters: meters(financialRows, financialTotal),
    };
  }

  function sumKrw(rows) {
    return rows.reduce(function (sum, row) {
      return sum + row.krw;
    }, 0);
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
    // 성장 가능성 축은 내렸다. 금융자산 기준으로 재보니 위험자산('높음') 비중과 같은
    // 값이 나온다 — 검증 포트폴리오에서 둘 다 53.8%로 완전히 일치했고, 무작위 표본
    // 5,000개 중 29%에서 두 값이 소수점까지 동일했다(r=0.80). 금융자산 6개 자산군 중
    // '중간'이 나올 수 있는 건 혼합형 펀드와 회사채뿐이고, 현금·예적금은 항상 '낮음',
    // 주식·ETF·가상자산은 항상 '높음'이라 그 둘이 없으면 정의상 같은 숫자다.
    // 축을 하나 더 그려도 같은 사실을 두 번 말하는 것뿐이라 뺐다.
    // (row.growth 분류 자체는 남겨둔다 — 업종 데이터로 주식 내부를 세분화하면 이 축이
    //  독립적인 정보를 갖게 되고, 그때 이 배열에 한 줄 추가하면 된다.)
    return [
      { key: "guaranteed", label: "원금 보장 비중", value: guaranteed / total, note: "원화 현금·예적금 ÷ 금융자산" },
      { key: "liquidity", label: "현금화 용이성", value: instant / total, note: "즉시 현금화 가능 자산 ÷ 금융자산" },
      { key: "concentration", label: "집중도", value: top3 / total, note: "상위 3개 자산 비중 합" },
    ];
  }

  global.Portfolio = {
    GROUP_NAMES: GROUP_NAMES,
    ASSET_CLASS: ASSET_CLASS,
    summarize: summarize,
    assetName: assetName,
    principalGuaranteed: principalGuaranteed,
    liquidity: liquidity,
    growth: growth,
    currencyLabel: currencyLabel,
  };
})(window);
