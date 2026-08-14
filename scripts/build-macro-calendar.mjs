// scripts/build-macro-calendar.mjs
//
// data/macro-calendar.json 생성기. 외부 API를 부르지 않는다 — 날짜는 전부 아래 표에
// 손으로 적혀 있고, 이 스크립트가 하는 일은 두 가지뿐이다.
//
//   1) 미국 현지 시각(ET)을 한국 시각으로 정확히 환산한다.
//   2) 지표 종류별 시나리오 문구를 붙이고 정렬·검증한다.
//
// 왜 손으로 적은 날짜에 생성기가 필요한가: 이 파일에서 틀리기 쉬운 것은 날짜가 아니라
// 시간대 환산이다. 08:30 ET는 한국에서 같은 날 저녁이지만 14:00 ET(FOMC)는 다음 날
// 새벽이다. 서머타임 경계(2026-11-01)를 넘으면 그 값이 또 한 시간씩 밀린다. 손으로
// 적으면 반드시 어딘가 하루가 어긋나므로 IANA 시간대 규칙에 맡긴다.
//
// 갱신 방법: 아래 표에 새 날짜를 추가하고 `node scripts/build-macro-calendar.mjs`.
// 각 표 위에 출처 URL이 있다. 기관이 새 연도 일정을 공표하면 그 페이지에서 옮겨 적고
// COVERAGE의 마지막 날짜도 함께 올린다 — 안 올리면 화면이 만료를 알리지 못한다.

import { writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = resolve(ROOT, "data", "macro-calendar.json");

// ─────────────────────────────────────────────────────────────────────────────
// 시간대 환산
// ─────────────────────────────────────────────────────────────────────────────

// 특정 순간에 그 시간대가 UTC로부터 몇 분 떨어져 있는지. 서머타임을 직접 계산하지
// 않고 IANA 규칙에 물어본다.
function tzOffsetMinutes(timeZone, instant) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone, hour12: false,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }).formatToParts(instant).map((part) => [part.type, part.value])
  );
  const asUtc = Date.UTC(
    Number(parts.year), Number(parts.month) - 1, Number(parts.day),
    Number(parts.hour) % 24, Number(parts.minute), Number(parts.second)
  );
  return (asUtc - instant.getTime()) / 60000;
}

// "그 지역의 벽시계로 이 날짜 이 시각"이 가리키는 UTC 순간.
function wallClockToInstant(timeZone, date, time) {
  const naive = new Date(`${date}T${time}:00Z`);
  let offset = tzOffsetMinutes(timeZone, naive);
  let instant = new Date(naive.getTime() - offset * 60000);
  // 서머타임 경계 근처에서는 한 번 더 맞춰야 한다(추정한 오프셋이 경계 반대쪽일 수 있다).
  const corrected = tzOffsetMinutes(timeZone, instant);
  if (corrected !== offset) instant = new Date(naive.getTime() - corrected * 60000);
  return instant;
}

// UTC 순간을 "+09:00"이 붙은 한국 시각 문자열로.
function toKstIso(instant) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Seoul", hour12: false,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }).formatToParts(instant).map((part) => [part.type, part.value])
  );
  const hour = String(Number(parts.hour) % 24).padStart(2, "0");
  return `${parts.year}-${parts.month}-${parts.day}T${hour}:${parts.minute}:00+09:00`;
}

const etToKst = (date, time) => toKstIso(wallClockToInstant("America/New_York", date, time));
const kstLocal = (date, time) => `${date}T${time}:00+09:00`;

// ─────────────────────────────────────────────────────────────────────────────
// 시나리오
//
// 규제 경계: 일반적으로 알려진 파급 경로만 적는다. 매매·비중 조정 지시, 읽는 사람의
// 보유 자산 언급, 어느 쪽이 될지에 대한 예측이나 확률은 넣지 않는다. 회차마다
// 메커니즘이 달라지지 않으므로 지표 종류별로 같은 문구를 쓴다 — 회차별로 다르게 쓰면
// 있지도 않은 구체성을 지어내는 셈이다. 이 금지 표현은 회귀 테스트가 검사한다.
// ─────────────────────────────────────────────────────────────────────────────

const SCENARIOS = {
  "rate-fomc": [
    ["정책금리를 올리는 경우", "금리가 오르면 일반적으로 채권 가격은 하락하고, 먼 미래의 이익을 크게 반영하는 성장주는 밸류에이션 압박을 받습니다. 달러 자금의 조달 비용이 높아지면서 통상 달러가 강세를 보이고, 원·달러 환율이 오르면 국내 투자자에게 외화 자산의 원화 환산액은 커집니다. 금리가 오르는 국면에서는 이자를 주지 않는 자산이 상대적으로 불리해지는 경향이 있습니다."],
    ["정책금리를 내리는 경우", "금리가 내리면 일반적으로 기존에 발행된 채권의 가격은 상승하고, 할인율이 낮아지면서 성장주의 밸류에이션 부담은 줄어듭니다. 달러 강세 압력이 완화되면 신흥국으로 자금이 유입되는 경향이 있습니다. 다만 금리 인하가 경기 둔화에 대응하는 것이라면 기업 이익 전망이 함께 나빠질 수 있어, 금리 효과와 경기 효과가 반대 방향으로 작용하기도 합니다."],
  ],
  "rate-bok": [
    ["기준금리를 올리는 경우", "기준금리가 오르면 예금·적금 금리와 대출 금리가 함께 오르는 경향이 있고, 기존 채권의 가격은 일반적으로 하락합니다. 차입 비용이 커지면서 부채 비율이 높은 기업과 부동산처럼 대출을 끼고 보유하는 자산은 부담이 커집니다. 한미 금리차가 좁혀지면 원화 강세 요인으로 작용하기도 합니다."],
    ["기준금리를 내리는 경우", "기준금리가 내리면 예금 금리는 낮아지고 기존 채권의 가격은 일반적으로 상승합니다. 차입 비용이 낮아지면 부채가 많은 기업과 부동산 시장의 이자 부담은 줄어듭니다. 다만 한미 금리차가 벌어지면 원화 약세 요인으로 작용해 수입 물가와 외화 자산의 원화 환산액에 영향을 줄 수 있습니다."],
  ],
  "cpi-us": [
    ["물가 상승률이 시장 예상을 웃도는 경우", "물가가 예상보다 높게 나오면 통화정책이 긴축적으로 유지되는 쪽으로 해석되는 경향이 있습니다. 시장금리가 오르면 기존 채권 가격은 일반적으로 하락하고, 할인율 상승은 먼 미래의 이익 비중이 큰 성장주에 부담으로 작용합니다. 달러 강세 요인으로 작용해 원·달러 환율이 오르면 국내 투자자에게 외화 자산의 원화 환산액은 커집니다."],
    ["물가 상승률이 시장 예상을 밑도는 경우", "물가가 예상보다 낮게 나오면 통화정책을 완화할 여지가 넓어지는 쪽으로 해석되는 경향이 있습니다. 시장금리가 내리면 기존 채권 가격은 일반적으로 상승하고 성장주의 밸류에이션 부담은 줄어듭니다. 다만 물가 둔화가 수요 위축에서 온 것이라면 기업 이익 전망이 함께 나빠질 수 있어, 금리 효과와 경기 효과가 반대 방향으로 작용하기도 합니다."],
  ],
  "pce-us": [
    ["물가 상승률이 시장 예상을 웃도는 경우", "PCE 물가는 연준이 물가 목표를 판단할 때 기준으로 삼는 지표입니다. 이 지표가 예상보다 높게 나오면 긴축이 길어지는 쪽으로 해석되는 경향이 있고, 시장금리 상승은 채권 가격 하락과 성장주 밸류에이션 부담으로 이어집니다. 달러 강세 요인으로 작용하면 원·달러 환율에도 영향을 줍니다."],
    ["물가 상승률이 시장 예상을 밑도는 경우", "PCE 물가가 예상보다 낮게 나오면 정책 완화 여지가 넓어지는 쪽으로 해석되는 경향이 있습니다. 시장금리가 내리면 기존 채권 가격은 일반적으로 상승합니다. 이 발표에는 소비지출 항목이 함께 담기므로, 물가가 둔화한 배경이 소비 위축이라면 경기 신호는 반대로 읽히기도 합니다."],
  ],
  "cpi-kr": [
    ["물가 상승률이 높게 나오는 경우", "국내 물가가 높게 나오면 한국은행이 기준금리를 낮추기 어려운 환경으로 해석되는 경향이 있습니다. 시중금리가 오르면 예금 금리는 오르고 기존 채권의 가격은 일반적으로 하락하며, 대출 이자 부담이 큰 가계·기업과 대출을 끼고 보유하는 부동산에는 부담으로 작용합니다. 물가 상승은 같은 금액으로 살 수 있는 양을 줄이므로 현금성 자산의 실질 가치도 함께 깎입니다."],
    ["물가 상승률이 낮게 나오는 경우", "국내 물가가 낮게 나오면 통화정책을 완화할 여지가 넓어지는 쪽으로 해석되는 경향이 있습니다. 시중금리가 내리면 예금 금리는 낮아지고 기존 채권의 가격은 일반적으로 상승합니다. 다만 물가 둔화가 내수 위축에서 온 것이라면 기업 실적 전망이 함께 나빠질 수 있어, 금리 효과와 경기 효과가 반대 방향으로 작용하기도 합니다."],
  ],
  "jobs-us": [
    ["고용이 시장 예상보다 강하게 나오는 경우", "고용이 강하면 경기 확장 신호로 읽히지만, 동시에 임금과 물가 압력으로 이어져 통화정책 완화 시점이 늦춰지는 쪽으로 해석되기도 합니다. 시장금리가 오르면 채권 가격은 일반적으로 하락하고 달러 강세 요인으로 작용합니다. 경기 신호와 금리 신호가 서로 반대 방향으로 읽히는 대표적인 지표입니다."],
    ["고용이 시장 예상보다 약하게 나오는 경우", "고용이 약하면 통화정책을 완화할 여지가 넓어지는 쪽으로 해석되는 경향이 있고, 시장금리가 내리면 기존 채권 가격은 일반적으로 상승합니다. 다만 고용 둔화는 소비와 기업 이익 전망을 함께 끌어내리는 요인이기도 해서, 금리 효과와 경기 효과가 반대로 작용하기도 합니다."],
  ],
  "jobs-kr": [
    ["취업자 증가폭이 커지는 경우", "고용이 개선되면 가계 소득과 소비 여력이 늘어나는 쪽으로 해석되는 경향이 있고, 내수와 관련된 기업 실적에 우호적인 요인으로 읽힙니다. 다만 고용과 물가가 함께 강해지면 한국은행이 기준금리를 낮추기 어려운 환경으로 해석되기도 합니다."],
    ["취업자 증가폭이 줄어드는 경우", "고용이 둔화되면 가계 소득과 소비가 함께 약해지는 쪽으로 읽히고, 내수 비중이 큰 업종에는 부담 요인으로 작용합니다. 반대로 통화정책을 완화할 여지는 넓어지는 쪽으로 해석되기도 해서, 경기 신호와 금리 신호가 반대 방향으로 나타나기도 합니다."],
  ],
  "gdp-us": [
    ["성장률이 시장 예상을 웃도는 경우", "성장률이 높게 나오면 기업 이익 기반이 탄탄하다는 신호로 읽히는 경향이 있습니다. 다만 경기가 강하면 통화정책 완화 시점이 늦춰지는 쪽으로도 해석돼 시장금리가 오르고, 그 경우 기존 채권 가격은 일반적으로 하락합니다. 달러 강세 요인으로 작용하면 원·달러 환율에도 영향을 줍니다."],
    ["성장률이 시장 예상을 밑도는 경우", "성장률이 낮게 나오면 기업 이익 전망이 함께 낮아지는 쪽으로 읽힙니다. 반면 통화정책 완화 여지는 넓어지는 쪽으로 해석돼 시장금리가 내리고 기존 채권 가격은 일반적으로 상승합니다. 경기 효과와 금리 효과가 서로 반대 방향으로 작용하는 구간입니다."],
  ],
  "gdp-kr": [
    ["성장률이 높게 나오는 경우", "국내 성장률이 높게 나오면 기업 실적과 세수 기반이 개선되는 쪽으로 읽히고, 원화에는 강세 요인으로 작용하기도 합니다. 다만 경기와 물가가 함께 강해지면 한국은행이 기준금리를 낮추기 어려운 환경으로 해석되기도 합니다."],
    ["성장률이 낮게 나오는 경우", "국내 성장률이 낮게 나오면 기업 실적 전망이 함께 낮아지고 원화에는 약세 요인으로 작용하기도 합니다. 원화가 약해지면 수입 물가와 외화 자산의 원화 환산액이 함께 올라갑니다. 반대로 통화정책을 완화할 여지는 넓어지는 쪽으로 해석되기도 합니다."],
  ],
  "retail-us": [
    ["소매판매가 시장 예상을 웃도는 경우", "미국 경제에서 소비가 차지하는 비중이 크기 때문에 소매판매는 경기 흐름을 빠르게 보여주는 지표로 읽힙니다. 소비가 강하면 기업 매출 기반이 탄탄하다는 신호로 해석되는 한편, 물가 압력과 맞물려 통화정책 완화 시점이 늦춰지는 쪽으로도 읽혀 시장금리가 오르기도 합니다."],
    ["소매판매가 시장 예상을 밑도는 경우", "소비가 약하면 기업 매출과 이익 전망이 함께 낮아지는 쪽으로 읽힙니다. 반면 수요 둔화는 물가 압력을 낮추는 요인이라 통화정책 완화 여지가 넓어지는 쪽으로 해석되기도 합니다. 경기 신호와 금리 신호가 반대로 나타나는 구간입니다."],
  ],
  "ism": [
    ["지수가 기준선 50을 넘는 경우", "ISM 지수는 50을 기준으로 확장과 위축을 나눕니다. 50을 넘으면 해당 부문의 활동이 늘어나는 국면으로 읽히고, 경기 민감 업종의 실적 기반에 우호적인 신호로 해석되는 경향이 있습니다. 다만 경기가 강하면 통화정책 완화 시점이 늦춰지는 쪽으로도 읽혀 시장금리가 오르기도 합니다."],
    ["지수가 기준선 50을 밑도는 경우", "50을 밑돌면 해당 부문의 활동이 줄어드는 국면으로 읽히고, 경기 민감 업종에는 부담 요인으로 해석되는 경향이 있습니다. 반면 수요 둔화는 물가 압력을 낮추는 요인이라 통화정책 완화 여지가 넓어지는 쪽으로 읽히기도 합니다."],
  ],
  "trade-kr": [
    ["수출이 늘어나는 경우", "한국은 수출이 경기에서 차지하는 비중이 큽니다. 수출이 늘면 반도체·자동차·화학처럼 수출 비중이 높은 업종의 실적 기반이 개선되는 쪽으로 읽히고, 무역수지 개선은 원화 강세 요인으로 작용하기도 합니다. 원화가 강해지면 외화 자산의 원화 환산액은 줄어듭니다."],
    ["수출이 줄어드는 경우", "수출이 줄면 수출 비중이 높은 업종의 실적 전망이 함께 낮아지는 쪽으로 읽힙니다. 무역수지가 나빠지면 원화 약세 요인으로 작용하기도 하며, 원화가 약해지면 수입 물가와 외화 자산의 원화 환산액이 함께 올라갑니다."],
  ],
  "current-account-kr": [
    ["경상수지 흑자가 커지는 경우", "경상수지는 한 나라가 대외 거래로 벌어들인 외화의 크기를 보여줍니다. 흑자가 커지면 외화가 국내로 들어오는 힘이 세져 원화 강세 요인으로 작용하는 경향이 있고, 원화가 강해지면 수입 물가와 외화 자산의 원화 환산액은 함께 낮아집니다."],
    ["경상수지 흑자가 줄거나 적자로 돌아서는 경우", "흑자가 줄거나 적자가 되면 외화가 빠져나가는 쪽으로 힘이 실려 원화 약세 요인으로 작용하는 경향이 있습니다. 원화가 약해지면 수입 물가가 오르고 외화 자산의 원화 환산액은 커집니다. 대외 신인도와 관련된 지표라 국채 금리에도 영향을 줍니다."],
  ],
};

// ─────────────────────────────────────────────────────────────────────────────
// 일정표 (손으로 유지)
// ─────────────────────────────────────────────────────────────────────────────

// 미국 — 시각은 전부 현지(ET). 08:30 ET는 한국에서 같은 날 저녁, 14:00 ET(FOMC)는
// 다음 날 새벽이다. 환산은 위 etToKst가 한다.
//
// FOMC   https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
// CPI·고용  미 노동통계국(BLS) 공표일정. bls.gov가 자동 조회를 차단(HTTP 403)해
//          BLS 일정을 그대로 옮겨 싣는 usinflationcalculator.com/financecalendar.com에서
//          확인했다. 갱신할 때는 bls.gov/schedule/news_release/ 를 브라우저로 직접 볼 것.
// GDP·PCE  https://www.bea.gov/news/schedule
// 소매판매  https://www.census.gov/economic-indicators/calendar-listview.html
// ISM     https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar/
//         ← 이 달력은 로그인을 요구해 열지 못했다. 그래서 ISM이 공표한 규칙
//         (제조업 = 매월 첫 영업일, 서비스업 = 셋째 영업일, 10:00 ET)으로 날짜를 잡고
//         dateBasis를 "rule"로 표시한다. 공휴일에 밀릴 수 있다는 뜻이고, 화면도 그렇게 적는다.
const US_EVENTS = [
  // FOMC. 지난 회차도 지운다 — 화면은 미래만 보여주지만, 지난 일정이 하나도 없으면
  // "지난 것은 걸러진다"는 회귀 테스트가 성립하지 않는다.
  ["fomc", "2026-01-28", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2026-03-18", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2026-04-29", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2026-06-17", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2026-07-29", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2026-09-16", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2026-10-28", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2026-12-09", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2027-01-27", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2027-03-17", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2027-04-28", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2027-06-09", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2027-07-28", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2027-09-15", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2027-10-27", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],
  ["fomc", "2027-12-08", "14:00", "FOMC", "rate", "high", "미국 FOMC 정책금리 결정", "rate-fomc", "published"],

  // 소비자물가지수 (BLS, 08:30 ET)
  ["us-cpi", "2026-09-11", "08:30", "BLS", "inflation", "high", "미국 소비자물가지수(CPI) — 8월", "cpi-us", "published"],
  ["us-cpi", "2026-10-14", "08:30", "BLS", "inflation", "high", "미국 소비자물가지수(CPI) — 9월", "cpi-us", "published"],
  ["us-cpi", "2026-11-10", "08:30", "BLS", "inflation", "high", "미국 소비자물가지수(CPI) — 10월", "cpi-us", "published"],
  ["us-cpi", "2026-12-10", "08:30", "BLS", "inflation", "high", "미국 소비자물가지수(CPI) — 11월", "cpi-us", "published"],

  // 고용보고서 (BLS, 08:30 ET)
  ["us-jobs", "2026-09-04", "08:30", "BLS", "employment", "high", "미국 고용보고서(비농업부문 고용) — 8월", "jobs-us", "published"],
  ["us-jobs", "2026-10-02", "08:30", "BLS", "employment", "high", "미국 고용보고서(비농업부문 고용) — 9월", "jobs-us", "published"],
  ["us-jobs", "2026-11-06", "08:30", "BLS", "employment", "high", "미국 고용보고서(비농업부문 고용) — 10월", "jobs-us", "published"],
  ["us-jobs", "2026-12-04", "08:30", "BLS", "employment", "high", "미국 고용보고서(비농업부문 고용) — 11월", "jobs-us", "published"],

  // 개인소비지출 물가 (BEA, 08:30 ET)
  ["us-pce", "2026-08-26", "08:30", "BEA", "inflation", "high", "미국 PCE 물가·개인소비지출 — 7월", "pce-us", "published"],
  ["us-pce", "2026-09-30", "08:30", "BEA", "inflation", "high", "미국 PCE 물가·개인소비지출 — 8월", "pce-us", "published"],
  ["us-pce", "2026-10-29", "08:30", "BEA", "inflation", "high", "미국 PCE 물가·개인소비지출 — 9월", "pce-us", "published"],
  ["us-pce", "2026-11-25", "08:30", "BEA", "inflation", "high", "미국 PCE 물가·개인소비지출 — 10월", "pce-us", "published"],
  ["us-pce", "2026-12-23", "08:30", "BEA", "inflation", "high", "미국 PCE 물가·개인소비지출 — 11월", "pce-us", "published"],

  // 국내총생산 (BEA, 08:30 ET)
  ["us-gdp", "2026-08-26", "08:30", "BEA", "growth", "medium", "미국 GDP 잠정치 — 2분기", "gdp-us", "published"],
  ["us-gdp", "2026-09-30", "08:30", "BEA", "growth", "medium", "미국 GDP 확정치 — 2분기", "gdp-us", "published"],
  ["us-gdp", "2026-10-29", "08:30", "BEA", "growth", "high", "미국 GDP 속보치 — 3분기", "gdp-us", "published"],
  ["us-gdp", "2026-11-25", "08:30", "BEA", "growth", "medium", "미국 GDP 잠정치 — 3분기", "gdp-us", "published"],
  ["us-gdp", "2026-12-23", "08:30", "BEA", "growth", "medium", "미국 GDP 확정치 — 3분기", "gdp-us", "published"],

  // 소매판매 (Census, 08:30 ET)
  ["us-retail", "2026-09-16", "08:30", "CENSUS", "growth", "medium", "미국 소매판매 — 8월", "retail-us", "published"],
  ["us-retail", "2026-10-15", "08:30", "CENSUS", "growth", "medium", "미국 소매판매 — 9월", "retail-us", "published"],
  ["us-retail", "2026-11-17", "08:30", "CENSUS", "growth", "medium", "미국 소매판매 — 10월", "retail-us", "published"],
  ["us-retail", "2026-12-16", "08:30", "CENSUS", "growth", "medium", "미국 소매판매 — 11월", "retail-us", "published"],

  // ISM (10:00 ET) — 규칙 기반. 공휴일에 밀릴 수 있다.
  ["us-ism-mfg", "2026-09-01", "10:00", "ISM", "growth", "medium", "미국 ISM 제조업 PMI — 8월", "ism", "rule"],
  ["us-ism-svc", "2026-09-03", "10:00", "ISM", "growth", "medium", "미국 ISM 서비스업 PMI — 8월", "ism", "rule"],
  ["us-ism-mfg", "2026-10-01", "10:00", "ISM", "growth", "medium", "미국 ISM 제조업 PMI — 9월", "ism", "rule"],
  ["us-ism-svc", "2026-10-05", "10:00", "ISM", "growth", "medium", "미국 ISM 서비스업 PMI — 9월", "ism", "rule"],
  ["us-ism-mfg", "2026-11-02", "10:00", "ISM", "growth", "medium", "미국 ISM 제조업 PMI — 10월", "ism", "rule"],
  ["us-ism-svc", "2026-11-04", "10:00", "ISM", "growth", "medium", "미국 ISM 서비스업 PMI — 10월", "ism", "rule"],
  ["us-ism-mfg", "2026-12-01", "10:00", "ISM", "growth", "medium", "미국 ISM 제조업 PMI — 11월", "ism", "rule"],
  ["us-ism-svc", "2026-12-03", "10:00", "ISM", "growth", "medium", "미국 ISM 서비스업 PMI — 11월", "ism", "rule"],
];

// 한국 — 시각은 전부 한국 시각이라 환산이 없다.
//
// 금통위    https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do?mtgSe=A&menuNo=200755
// GDP·경상수지 https://www.bok.or.kr/portal/stats/statsPublictSchdul/listCldr.do?menuNo=200775 (08:00)
// 물가·고용  국가데이터처 2026년 보도계획 https://www.mods.go.kr/ansk/file/schedule_2026.pdf
//           (공표일만 적혀 있고 시각은 없다. 보도자료 공표 시각 08:00을 쓴다.)
// 수출입동향 산업통상부. 매월 1일 공표 관례에 따른 예정일이라 dateBasis는 "rule"이다.
const KR_EVENTS = [
  // 금통위. 지난 회차를 남기는 이유는 위 US_EVENTS와 같다.
  ["bok-rate", "2026-01-15", "10:00", "BOK", "rate", "high", "한국은행 금통위 기준금리 결정", "rate-bok", "published", "통화정책방향 결정회의 · 오전 발표"],
  ["bok-rate", "2026-02-26", "10:00", "BOK", "rate", "high", "한국은행 금통위 기준금리 결정", "rate-bok", "published", "통화정책방향 결정회의 · 오전 발표"],
  ["bok-rate", "2026-04-10", "10:00", "BOK", "rate", "high", "한국은행 금통위 기준금리 결정", "rate-bok", "published", "통화정책방향 결정회의 · 오전 발표"],
  ["bok-rate", "2026-05-28", "10:00", "BOK", "rate", "high", "한국은행 금통위 기준금리 결정", "rate-bok", "published", "통화정책방향 결정회의 · 오전 발표"],
  ["bok-rate", "2026-07-16", "10:00", "BOK", "rate", "high", "한국은행 금통위 기준금리 결정", "rate-bok", "published", "통화정책방향 결정회의 · 오전 발표"],
  ["bok-rate", "2026-08-27", "10:00", "BOK", "rate", "high", "한국은행 금통위 기준금리 결정", "rate-bok", "published", "통화정책방향 결정회의 · 오전 발표"],
  ["bok-rate", "2026-10-22", "10:00", "BOK", "rate", "high", "한국은행 금통위 기준금리 결정", "rate-bok", "published", "통화정책방향 결정회의 · 오전 발표"],
  ["bok-rate", "2026-11-26", "10:00", "BOK", "rate", "high", "한국은행 금통위 기준금리 결정", "rate-bok", "published", "통화정책방향 결정회의 · 오전 발표"],

  // 소비자물가동향 (국가데이터처, 08:00)
  ["kr-cpi", "2026-09-02", "08:00", "KOSTAT", "inflation", "high", "한국 소비자물가동향 — 8월", "cpi-kr", "published", "국가데이터처 보도자료"],
  ["kr-cpi", "2026-10-02", "08:00", "KOSTAT", "inflation", "high", "한국 소비자물가동향 — 9월", "cpi-kr", "published", "국가데이터처 보도자료"],
  ["kr-cpi", "2026-11-03", "08:00", "KOSTAT", "inflation", "high", "한국 소비자물가동향 — 10월", "cpi-kr", "published", "국가데이터처 보도자료"],
  ["kr-cpi", "2026-12-02", "08:00", "KOSTAT", "inflation", "high", "한국 소비자물가동향 — 11월", "cpi-kr", "published", "국가데이터처 보도자료"],
  ["kr-cpi", "2026-12-31", "08:00", "KOSTAT", "inflation", "high", "한국 소비자물가동향 — 12월 및 연간", "cpi-kr", "published", "국가데이터처 보도자료"],

  // 고용동향 (국가데이터처, 08:00)
  ["kr-jobs", "2026-09-09", "08:00", "KOSTAT", "employment", "medium", "한국 고용동향 — 8월", "jobs-kr", "published", "국가데이터처 보도자료"],
  ["kr-jobs", "2026-10-16", "08:00", "KOSTAT", "employment", "medium", "한국 고용동향 — 9월", "jobs-kr", "published", "국가데이터처 보도자료"],
  ["kr-jobs", "2026-11-11", "08:00", "KOSTAT", "employment", "medium", "한국 고용동향 — 10월", "jobs-kr", "published", "국가데이터처 보도자료"],
  ["kr-jobs", "2026-12-16", "08:00", "KOSTAT", "employment", "medium", "한국 고용동향 — 11월", "jobs-kr", "published", "국가데이터처 보도자료"],

  // 국민소득 (한국은행, 08:00)
  ["kr-gdp", "2026-09-08", "08:00", "BOK", "growth", "medium", "한국 국민소득 잠정치 — 2분기", "gdp-kr", "published", "한국은행 국민계정"],
  ["kr-gdp", "2026-10-27", "08:00", "BOK", "growth", "high", "한국 실질 GDP 속보치 — 3분기", "gdp-kr", "published", "한국은행 국민계정"],
  ["kr-gdp", "2026-12-09", "08:00", "BOK", "growth", "medium", "한국 국민소득 잠정치 — 3분기", "gdp-kr", "published", "한국은행 국민계정"],

  // 국제수지 (한국은행, 08:00)
  ["kr-ca", "2026-09-04", "08:00", "BOK", "trade", "medium", "한국 경상수지 — 7월", "current-account-kr", "published", "한국은행 국제수지(잠정)"],
  ["kr-ca", "2026-10-08", "08:00", "BOK", "trade", "medium", "한국 경상수지 — 8월", "current-account-kr", "published", "한국은행 국제수지(잠정)"],
  ["kr-ca", "2026-11-05", "08:00", "BOK", "trade", "medium", "한국 경상수지 — 9월", "current-account-kr", "published", "한국은행 국제수지(잠정)"],
  ["kr-ca", "2026-12-08", "08:00", "BOK", "trade", "medium", "한국 경상수지 — 10월", "current-account-kr", "published", "한국은행 국제수지(잠정)"],

  // 수출입동향 (산업통상부, 09:00) — 매월 1일 공표 관례
  ["kr-trade", "2026-09-01", "09:00", "MOTIE", "trade", "medium", "한국 수출입 동향 — 8월", "trade-kr", "rule", "산업통상부 · 매월 1일 공표"],
  ["kr-trade", "2026-10-01", "09:00", "MOTIE", "trade", "medium", "한국 수출입 동향 — 9월", "trade-kr", "rule", "산업통상부 · 매월 1일 공표"],
  ["kr-trade", "2026-11-01", "09:00", "MOTIE", "trade", "medium", "한국 수출입 동향 — 10월", "trade-kr", "rule", "산업통상부 · 매월 1일 공표"],
  ["kr-trade", "2026-12-01", "09:00", "MOTIE", "trade", "medium", "한국 수출입 동향 — 11월", "trade-kr", "rule", "산업통상부 · 매월 1일 공표"],
];

// 기관별 커버리지. 이벤트 목록에서 자동으로 뽑지 않는다 — 마지막 일정은 언제나
// 마지막 일정이라 자동 계산은 "언제까지 공표됐는지"를 알려주지 못한다. 기관이 어디까지
// 공표했는지는 사람이 확인해서 여기에 적어야 한다.
const COVERAGE = {
  FOMC: "2027-12-09",
  BOK_RATE: "2026-11-26",
  KR_CPI: "2026-12-31",
  KR_JOBS: "2026-12-16",
  KR_GDP: "2026-12-09",
  KR_CA: "2026-12-08",
  KR_TRADE: "2026-12-01",
  US_CPI: "2026-12-10",
  US_JOBS: "2026-12-04",
  US_BEA: "2026-12-23",
  US_RETAIL: "2026-12-16",
  US_ISM: "2026-12-03",
};

const COVERAGE_LABELS = {
  FOMC: "미국 FOMC 금리 결정",
  BOK_RATE: "한국 금통위 금리 결정",
  KR_CPI: "한국 소비자물가",
  KR_JOBS: "한국 고용동향",
  KR_GDP: "한국 국민소득",
  KR_CA: "한국 경상수지",
  KR_TRADE: "한국 수출입 동향",
  US_CPI: "미국 소비자물가",
  US_JOBS: "미국 고용보고서",
  US_BEA: "미국 GDP·PCE",
  US_RETAIL: "미국 소매판매",
  US_ISM: "미국 ISM PMI",
};

const SOURCES = {
  FOMC: "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
  BOK: "https://www.bok.or.kr/portal/stats/statsPublictSchdul/listCldr.do?menuNo=200775",
  KOSTAT: "https://www.mods.go.kr/ansk/file/schedule_2026.pdf",
  MOTIE: "https://www.motir.go.kr",
  BLS: "https://www.bls.gov/schedule/news_release/",
  BEA: "https://www.bea.gov/news/schedule",
  CENSUS: "https://www.census.gov/economic-indicators/calendar-listview.html",
  ISM: "https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar/",
};

// ─────────────────────────────────────────────────────────────────────────────

function scenariosOf(key) {
  const pairs = SCENARIOS[key];
  if (!pairs) throw new Error(`시나리오 정의가 없습니다: ${key}`);
  return pairs.map(([label, text]) => ({ label, text }));
}

function buildUs([slug, date, time, org, category, importance, title, scenarioKey, dateBasis]) {
  const datetimeKst = etToKst(date, time);
  const sameDay = datetimeKst.slice(0, 10) === date;
  const note = `현지 ${date} ${time} 발표 · 한국 시각 ${sameDay ? "같은 날" : "다음 날"} 기준`
    + (dateBasis === "rule" ? " · ISM 공표 규칙에 따른 예정일(공휴일에 밀릴 수 있음)" : "");
  return { id: `${slug}-${date}`, datetimeKst, org, country: "USA", category, importance, dateBasis, title, note, scenarios: scenariosOf(scenarioKey) };
}

function buildKr([slug, date, time, org, category, importance, title, scenarioKey, dateBasis, note]) {
  return {
    id: `${slug}-${date}`,
    datetimeKst: kstLocal(date, time),
    org, country: "KOR", category, importance, dateBasis, title,
    note: dateBasis === "rule" ? `${note} 관례에 따른 예정일` : note,
    scenarios: scenariosOf(scenarioKey),
  };
}

const events = [...US_EVENTS.map(buildUs), ...KR_EVENTS.map(buildKr)]
  .sort((a, b) => a.datetimeKst.localeCompare(b.datetimeKst));

// --- 검증. 여기서 막지 못하면 화면에 틀린 날짜가 그대로 나간다. ---
const problems = [];

const seen = new Set();
events.forEach((event) => {
  if (seen.has(event.id)) problems.push(`중복 id: ${event.id}`);
  seen.add(event.id);
  if (event.scenarios.length !== 2) problems.push(`${event.id}: 시나리오 ${event.scenarios.length}개`);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00\+09:00$/.test(event.datetimeKst)) problems.push(`${event.id}: 시각 형식 ${event.datetimeKst}`);
});

// 규제 경계. 회귀 테스트와 같은 패턴을 여기서도 본다 — 배치가 먼저 막는 편이 낫다.
const FORBIDDEN = /(매수|매도|사세요|파세요|줄이세요|늘리세요|비중을 조정|리밸런싱|보유하신|귀하의|확률|가능성이 높|전망합니다|예상됩니다)/;
events.forEach((event) => event.scenarios.forEach((scenario) => {
  if (FORBIDDEN.test(scenario.label) || FORBIDDEN.test(scenario.text)) problems.push(`${event.id}: 금지 표현 (${scenario.label})`);
}));

// 시간대 환산이 실제로 갈리는지 본다. 08:30 ET는 한국에서 같은 날, 14:00 ET는 다음 날이어야
// 한다. 하나의 오프셋을 통째로 적용하는 실수를 여기서 잡는다.
const fomc = events.filter((event) => event.org === "FOMC");
fomc.forEach((event) => {
  const usDate = /현지 (\d{4}-\d{2}-\d{2})/.exec(event.note)[1];
  if (event.datetimeKst.slice(0, 10) <= usDate) problems.push(`${event.id}: FOMC인데 KST 날짜가 다음 날이 아님`);
  const hour = Number(event.datetimeKst.slice(11, 13));
  if (hour !== 3 && hour !== 4) problems.push(`${event.id}: FOMC 시각 ${hour}시 (3 또는 4 기대)`);
});
events.filter((event) => event.country === "USA" && /08:30 발표/.test(event.note || "")).forEach((event) => {
  const usDate = /현지 (\d{4}-\d{2}-\d{2})/.exec(event.note)[1];
  if (event.datetimeKst.slice(0, 10) !== usDate) problems.push(`${event.id}: 08:30 ET인데 KST 날짜가 다름`);
  const hour = Number(event.datetimeKst.slice(11, 13));
  if (hour !== 21 && hour !== 22) problems.push(`${event.id}: 08:30 ET 환산 시각 ${hour}시 (21 또는 22 기대)`);
});

if (problems.length) {
  problems.forEach((line) => console.error(`[검증 실패] ${line}`));
  process.exit(1);
}

const coverageUntil = Object.values(COVERAGE).sort().at(-1);
const payload = {
  coverageUntil,
  coverage: COVERAGE,
  coverageLabels: COVERAGE_LABELS,
  sources: SOURCES,
  updatedAt: new Date().toISOString().slice(0, 10),
  generatedBy: "scripts/build-macro-calendar.mjs",
  events,
};

await writeFile(OUT, `${JSON.stringify(payload, null, 2)}\n`, "utf8");

const byCountry = events.reduce((acc, event) => ({ ...acc, [event.country]: (acc[event.country] || 0) + 1 }), {});
const ruleBased = events.filter((event) => event.dateBasis === "rule").length;
console.log(`일정 ${events.length}건 (한국 ${byCountry.KOR} · 미국 ${byCountry.USA}) · 규칙 기반 예정일 ${ruleBased}건`);
console.log(`기간 ${events[0].datetimeKst.slice(0, 10)} ~ ${events.at(-1).datetimeKst.slice(0, 10)} · coverageUntil ${coverageUntil}`);
console.log(`08:30 ET 예시  ${events.find((event) => /08:30 발표/.test(event.note || "")).id} → ${events.find((event) => /08:30 발표/.test(event.note || "")).datetimeKst}`);
console.log(`14:00 ET 예시  ${fomc[0].id} → ${fomc[0].datetimeKst}`);
