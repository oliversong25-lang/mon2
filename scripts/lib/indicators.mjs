// scripts/lib/indicators.mjs
// 어떤 지표를 어느 계열에서 뽑을지 정의한다. 선택자(selector)는 실제 응답을 디코딩해
// 확인한 값이다 — 예를 들어 CLI는 진폭조정(AA)·지수(IX)·OECD 조화(H) 조합이어야
// 흔히 말하는 "경기선행지수"가 나온다.
//
// 나중에 ECOS·FRED를 붙일 때 이 표에 source 필드가 다른 항목을 더하면 되도록,
// 지표 정의와 출처를 분리해 둔다. 기존 파일 모양은 바꾸지 않는다.

export const SOURCE_OECD = "OECD";

// 자리 수는 422 응답이 알려준 값이다(모자라면 "expecting N got 1").
export const DATAFLOWS = {
  DF_CLI: { id: "OECD.SDD.STES,DSD_STES@DF_CLI,4.1", keyLength: 9, file: "DF_CLI" },
  DF_INDSERV: { id: "OECD.SDD.STES,DSD_STES@DF_INDSERV,4.3", keyLength: 9, file: "DF_INDSERV" },
  DF_UNE: { id: "OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0", keyLength: 9, file: "DF_IALFS_UNE_M" },
  DF_CPI: { id: "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0", keyLength: 8, file: "DF_PRICES_ALL" },
};

// selector의 각 항목은 "이 차원이 이 값인 행만 고른다"는 뜻이다.
export const INDICATORS = [
  {
    id: "cli", source: SOURCE_OECD, dataflow: "DF_CLI",
    nameKo: "경기선행지수", nameEn: "Composite Leading Indicator (CLI)",
    unitKo: "지수(장기평균=100)", freq: "M", headline: true,
    selector: { MEASURE: "LI", ADJUSTMENT: "AA", TRANSFORMATION: "IX", METHODOLOGY: "H", FREQ: "M" },
  },
  {
    id: "cci", source: SOURCE_OECD, dataflow: "DF_CLI",
    nameKo: "소비자심리지수", nameEn: "Consumer Confidence Index (CCI)",
    unitKo: "지수(장기평균=100)", freq: "M", headline: true,
    selector: { MEASURE: "CCICP", ADJUSTMENT: "AA", TRANSFORMATION: "IX", METHODOLOGY: "H", FREQ: "M" },
  },
  {
    id: "bci", source: SOURCE_OECD, dataflow: "DF_CLI",
    nameKo: "기업심리지수", nameEn: "Business Confidence Index (BCI)",
    unitKo: "지수(장기평균=100)", freq: "M", headline: true,
    selector: { MEASURE: "BCICP", ADJUSTMENT: "AA", TRANSFORMATION: "IX", METHODOLOGY: "H", FREQ: "M" },
  },
  {
    id: "unemployment", source: SOURCE_OECD, dataflow: "DF_UNE",
    nameKo: "실업률", nameEn: "Unemployment rate",
    unitKo: "%", freq: "M", headline: true,
    selector: { MEASURE: "UNE_LF_M", UNIT_MEASURE: "PT_LF_SUB", ADJUSTMENT: "Y", SEX: "_T", AGE: "Y_GE15", FREQ: "M" },
  },
  {
    id: "cpi", source: SOURCE_OECD, dataflow: "DF_CPI",
    nameKo: "소비자물가 상승률", nameEn: "Consumer price index (YoY)",
    unitKo: "% (전년동월대비)", freq: "M", headline: true,
    selector: { MEASURE: "CPI", EXPENDITURE: "_T", TRANSFORMATION: "GY", METHODOLOGY: "N", ADJUSTMENT: "N", FREQ: "M" },
  },
  {
    id: "industrial-production", source: SOURCE_OECD, dataflow: "DF_INDSERV",
    nameKo: "산업생산지수", nameEn: "Industrial production",
    unitKo: "지수(2021=100)", freq: "M", headline: false,
    selector: { MEASURE: "PRVM", ACTIVITY: "BTE", ADJUSTMENT: "Y", FREQ: "M", UNIT_MEASURE: "IX" },
  },
  {
    id: "retail-trade", source: SOURCE_OECD, dataflow: "DF_INDSERV",
    nameKo: "소매판매지수", nameEn: "Retail trade",
    unitKo: "지수(2021=100)", freq: "M", headline: false,
    selector: { MEASURE: "TOVM", ACTIVITY: "G47", ADJUSTMENT: "Y", FREQ: "M", UNIT_MEASURE: "IX" },
  },
];

// OECD 회원국 + 자주 보는 집계. 국가명 한글은 응답에 없어서 여기서만 관리한다.
export const COUNTRIES = {
  KOR: "대한민국", USA: "미국", JPN: "일본", DEU: "독일", FRA: "프랑스", GBR: "영국",
  ITA: "이탈리아", CAN: "캐나다", AUS: "호주", NZL: "뉴질랜드", ESP: "스페인",
  NLD: "네덜란드", BEL: "벨기에", AUT: "오스트리아", CHE: "스위스", SWE: "스웨덴",
  NOR: "노르웨이", DNK: "덴마크", FIN: "핀란드", ISL: "아이슬란드", IRL: "아일랜드",
  PRT: "포르투갈", GRC: "그리스", LUX: "룩셈부르크", POL: "폴란드", CZE: "체코",
  SVK: "슬로바키아", HUN: "헝가리", SVN: "슬로베니아", EST: "에스토니아",
  LVA: "라트비아", LTU: "리투아니아", TUR: "튀르키예", MEX: "멕시코", CHL: "칠레",
  COL: "콜롬비아", CRI: "코스타리카", ISR: "이스라엘",
  OECD: "OECD 전체", EA20: "유로존", EA19: "유로존(19개국)", G7: "주요 7개국(G7)", G20: "주요 20개국(G20)",
};

// 홈 카드에 올릴 소수의 지표. 카드 높이를 지켜야 하므로 의도적으로 적게 둔다.
export const HOME_HEADLINES = [
  { indicator: "cli", country: "KOR" },
  { indicator: "cpi", country: "KOR" },
  { indicator: "unemployment", country: "KOR" },
  { indicator: "cli", country: "USA" },
];

export function matchesSelector(row, selector) {
  return Object.entries(selector).every(([dimension, value]) => row[dimension] === value);
}
