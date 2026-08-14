// scripts/lib/indicators.mjs
// 어떤 지표를 어느 계열에서 뽑을지 정의한다. 선택자(selector)는 실제 응답을 디코딩해
// 확인한 값이다 — 예를 들어 CLI는 진폭조정(AA)·지수(IX)·OECD 조화(H) 조합이어야
// 흔히 말하는 "경기선행지수"가 나온다.
//
// 계열을 통째로 담지 않고 헤드라인만 고르는 이유: OECD 공개 엔드포인트에는 dataflow가
// 1,546개 있고, 거시·금융과 관련된 22개만 추려도 계열이 12만 개다. 10년치를 전부
// 담으면 150MB가 넘고 매일 배치가 그만큼을 커밋한다. 조정치·연령대별·품목별 분해는
// 담지 않고, 사람이 실제로 읽는 헤드라인 계열만 selector로 집는다.
//
// 나중에 ECOS·FRED를 붙일 때는 이 표에 source가 다른 항목을 더하면 된다. 지표 정의와
// 출처를 분리해 뒀고, 계열 id가 `<출처>:<지표>:<국가>`라 파일 모양을 바꾸지 않아도 된다.

export const SOURCE_OECD = "OECD";

// 주제는 화면의 1차 축이다. 국가는 필터이지 유일한 진입점이 아니다.
export const TOPICS = [
  { id: "prices", nameKo: "물가", nameEn: "Prices" },
  { id: "labour", nameKo: "고용", nameEn: "Labour" },
  { id: "cycle", nameKo: "경기", nameEn: "Business cycle" },
  { id: "output", nameKo: "생산", nameEn: "Production" },
  { id: "finance", nameKo: "금리·금융", nameEn: "Rates & markets" },
  { id: "trade", nameKo: "무역", nameEn: "Trade" },
  { id: "growth", nameKo: "성장", nameEn: "Growth" },
];

// 자리 수는 422 응답이 알려준 값이다(모자라면 "expecting N got 1").
// 버전도 반드시 맞아야 한다 — 전부 1.0으로 두면 STES 계열이 통째로 404난다.
export const DATAFLOWS = {
  DF_CLI: { id: "OECD.SDD.STES,DSD_STES@DF_CLI,4.1", keyLength: 9, file: "DF_CLI" },
  DF_BTS: { id: "OECD.SDD.STES,DSD_STES@DF_BTS,4.0", keyLength: 9, file: "DF_BTS" },
  DF_CS: { id: "OECD.SDD.STES,DSD_STES@DF_CS,4.0", keyLength: 9, file: "DF_CS" },
  DF_INDSERV: { id: "OECD.SDD.STES,DSD_STES@DF_INDSERV,4.3", keyLength: 9, file: "DF_INDSERV" },
  DF_FINMARK: { id: "OECD.SDD.STES,DSD_STES@DF_FINMARK,4.0", keyLength: 9, file: "DF_FINMARK" },
  DF_KEI: { id: "OECD.SDD.STES,DSD_KEI@DF_KEI,4.0", keyLength: 7, file: "DF_KEI" },
  DF_UNE: { id: "OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0", keyLength: 9, file: "DF_IALFS_UNE_M" },
  DF_EMP: { id: "OECD.SDD.TPS,DSD_LFS@DF_IALFS_EMP_WAP_Q,1.0", keyLength: 9, file: "DF_IALFS_EMP_WAP_Q" },
  DF_LF: { id: "OECD.SDD.TPS,DSD_LFS@DF_IALFS_LF_WAP_Q,1.0", keyLength: 9, file: "DF_IALFS_LF_WAP_Q" },
  DF_EAR: { id: "OECD.SDD.TPS,DSD_EAR@DF_HOU_EAR,1.0", keyLength: 9, file: "DF_HOU_EAR" },
  DF_CPI: { id: "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0", keyLength: 8, file: "DF_PRICES_ALL" },
  DF_IMTS: { id: "OECD.SDD.TPS,DSD_IMTS@DF_IMTS,1.0", keyLength: 8, file: "DF_IMTS" },
  DF_BOP: { id: "OECD.SDD.TPS,DSD_BOP@DF_BOP,1.0", keyLength: 8, file: "DF_BOP" },
};

// selector의 각 항목은 "이 차원이 이 값인 행만 고른다"는 뜻이다.
// 값은 전부 실제 코드리스트에서 확인한 것이다 — 추측한 값은 관측 0건으로 조용히 사라진다.
const OECD_INDICATORS = [
  // ── 물가 ──────────────────────────────────────────────────────────────────
  {
    id: "cpi", topic: "prices", dataflow: "DF_CPI",
    nameKo: "소비자물가 상승률", nameEn: "Consumer price inflation (YoY)",
    unitKo: "% (전년 동월 대비)", freq: "M", headline: true,
    selector: { MEASURE: "CPI", EXPENDITURE: "_T", TRANSFORMATION: "GY", METHODOLOGY: "N", ADJUSTMENT: "N", FREQ: "M" },
  },
  {
    id: "cpi-index", topic: "prices", dataflow: "DF_CPI",
    nameKo: "소비자물가지수", nameEn: "Consumer price index",
    unitKo: "지수", freq: "M", headline: false,
    selector: { MEASURE: "CPI", EXPENDITURE: "_T", TRANSFORMATION: "_Z", UNIT_MEASURE: "IX", METHODOLOGY: "N", ADJUSTMENT: "N", FREQ: "M" },
  },
  {
    id: "cpi-mom", topic: "prices", dataflow: "DF_CPI",
    nameKo: "소비자물가 상승률(전월비)", nameEn: "Consumer price inflation (MoM)",
    unitKo: "% (전월 대비)", freq: "M", headline: false,
    selector: { MEASURE: "CPI", EXPENDITURE: "_T", TRANSFORMATION: "G1", METHODOLOGY: "N", ADJUSTMENT: "N", FREQ: "M" },
  },
  {
    id: "cpi-core", topic: "prices", dataflow: "DF_CPI",
    nameKo: "근원물가 상승률", nameEn: "Core inflation (excl. food and energy)",
    unitKo: "% (전년 동월 대비)", freq: "M", headline: false,
    selector: { MEASURE: "CPI", EXPENDITURE: "_TXCP01_NRG", TRANSFORMATION: "GY", METHODOLOGY: "N", ADJUSTMENT: "N", FREQ: "M" },
  },
  {
    id: "cpi-food", topic: "prices", dataflow: "DF_CPI",
    nameKo: "식료품·비주류음료 물가", nameEn: "Food and non-alcoholic beverage prices",
    unitKo: "% (전년 동월 대비)", freq: "M", headline: false,
    selector: { MEASURE: "CPI", EXPENDITURE: "CP01", TRANSFORMATION: "GY", METHODOLOGY: "N", ADJUSTMENT: "N", FREQ: "M" },
  },
  {
    id: "cpi-energy", topic: "prices", dataflow: "DF_CPI",
    nameKo: "에너지 물가", nameEn: "Energy prices",
    unitKo: "% (전년 동월 대비)", freq: "M", headline: false,
    selector: { MEASURE: "CPI", EXPENDITURE: "CP045_0722", TRANSFORMATION: "GY", METHODOLOGY: "N", ADJUSTMENT: "N", FREQ: "M" },
  },
  {
    id: "cpi-services", topic: "prices", dataflow: "DF_CPI",
    nameKo: "서비스 물가", nameEn: "Services prices",
    unitKo: "% (전년 동월 대비)", freq: "M", headline: false,
    selector: { MEASURE: "CPI", EXPENDITURE: "SERV", TRANSFORMATION: "GY", METHODOLOGY: "N", ADJUSTMENT: "N", FREQ: "M" },
  },
  {
    id: "cpi-transport", topic: "prices", dataflow: "DF_CPI",
    nameKo: "교통 물가", nameEn: "Transport prices",
    unitKo: "% (전년 동월 대비)", freq: "M", headline: false,
    selector: { MEASURE: "CPI", EXPENDITURE: "CP07", TRANSFORMATION: "GY", METHODOLOGY: "N", ADJUSTMENT: "N", FREQ: "M" },
  },
  {
    id: "ppi", topic: "prices", dataflow: "DF_KEI",
    nameKo: "생산자물가 상승률", nameEn: "Producer price inflation",
    unitKo: "% (전년 동월 대비)", freq: "M", headline: false,
    selector: { MEASURE: "PP", UNIT_MEASURE: "GR", TRANSFORMATION: "GY", FREQ: "M" },
  },

  // ── 고용 ──────────────────────────────────────────────────────────────────
  {
    id: "unemployment", topic: "labour", dataflow: "DF_UNE",
    nameKo: "실업률", nameEn: "Unemployment rate",
    unitKo: "% (경제활동인구 대비)", freq: "M", headline: true,
    selector: { MEASURE: "UNE_LF_M", UNIT_MEASURE: "PT_LF_SUB", ADJUSTMENT: "Y", SEX: "_T", AGE: "Y_GE15", FREQ: "M" },
  },
  {
    id: "unemployment-youth", topic: "labour", dataflow: "DF_UNE",
    nameKo: "청년 실업률(15~24세)", nameEn: "Youth unemployment rate (15-24)",
    unitKo: "% (경제활동인구 대비)", freq: "M", headline: false,
    selector: { MEASURE: "UNE_LF_M", UNIT_MEASURE: "PT_LF_SUB", ADJUSTMENT: "Y", SEX: "_T", AGE: "Y15T24", FREQ: "M" },
  },
  {
    id: "employment-rate", topic: "labour", dataflow: "DF_EMP",
    nameKo: "고용률(15~64세)", nameEn: "Employment rate (15-64)",
    unitKo: "% (생산가능인구 대비)", freq: "Q", headline: false,
    selector: { MEASURE: "EMP_WAP", UNIT_MEASURE: "PT_WAP_SUB", ADJUSTMENT: "Y", SEX: "_T", AGE: "Y15T64", FREQ: "Q" },
  },
  {
    id: "participation-rate", topic: "labour", dataflow: "DF_LF",
    nameKo: "경제활동참가율(15~64세)", nameEn: "Labour force participation rate (15-64)",
    unitKo: "% (생산가능인구 대비)", freq: "Q", headline: false,
    selector: { MEASURE: "LF_WAP", UNIT_MEASURE: "PT_WAP_SUB", ADJUSTMENT: "Y", SEX: "_T", AGE: "Y15T64", FREQ: "Q" },
  },
  {
    id: "earnings", topic: "labour", dataflow: "DF_EAR",
    nameKo: "시간당 임금", nameEn: "Hourly earnings",
    unitKo: "지수", freq: "Q", headline: false,
    selector: { MEASURE: "EAR", UNIT_MEASURE: "IX", SECTOR: "S1", ADJUSTMENT: "Y", FREQ: "Q" },
  },
  {
    id: "unit-labour-cost", topic: "labour", dataflow: "DF_KEI",
    nameKo: "단위노동비용", nameEn: "Unit labour cost (YoY)",
    unitKo: "% (전년 동기 대비)", freq: "Q", headline: false,
    selector: { MEASURE: "ULC", UNIT_MEASURE: "GR", TRANSFORMATION: "GY", FREQ: "Q" },
  },

  // ── 경기 ──────────────────────────────────────────────────────────────────
  {
    id: "cli", topic: "cycle", dataflow: "DF_CLI",
    nameKo: "경기선행지수", nameEn: "Composite leading indicator (CLI)",
    unitKo: "지수(장기평균=100)", freq: "M", headline: true,
    selector: { MEASURE: "LI", ADJUSTMENT: "AA", TRANSFORMATION: "IX", METHODOLOGY: "H", FREQ: "M" },
  },
  {
    id: "bci", topic: "cycle", dataflow: "DF_CLI",
    nameKo: "기업심리지수", nameEn: "Business confidence index (BCI)",
    unitKo: "지수(장기평균=100)", freq: "M", headline: false,
    selector: { MEASURE: "BCICP", ADJUSTMENT: "AA", TRANSFORMATION: "IX", METHODOLOGY: "H", FREQ: "M" },
  },
  {
    id: "cci", topic: "cycle", dataflow: "DF_CLI",
    nameKo: "소비자심리지수", nameEn: "Consumer confidence index (CCI)",
    unitKo: "지수(장기평균=100)", freq: "M", headline: false,
    selector: { MEASURE: "CCICP", ADJUSTMENT: "AA", TRANSFORMATION: "IX", METHODOLOGY: "H", FREQ: "M" },
  },
  {
    id: "bts-production", topic: "cycle", dataflow: "DF_BTS",
    nameKo: "제조업 생산 전망", nameEn: "Manufacturing production outlook",
    unitKo: "% 포인트(응답 차이)", freq: "M", headline: false,
    selector: { MEASURE: "PR", ACTIVITY: "C", TIME_HORIZ: "FT", ADJUSTMENT: "Y", FREQ: "M" },
  },
  {
    // 수주잔량(OB)은 TIME_HORIZ=C로만 있고 한국이 제출하지 않는다(독일·미국만).
    // 수주 유입(OI)은 한국을 포함하므로 이쪽을 싣는다.
    id: "bts-orders", topic: "cycle", dataflow: "DF_BTS",
    nameKo: "제조업 수주 유입", nameEn: "Manufacturing orders inflow",
    unitKo: "% 포인트(응답 차이)", freq: "M", headline: false,
    selector: { MEASURE: "OI", ACTIVITY: "C", TIME_HORIZ: "T", ADJUSTMENT: "Y", FREQ: "M" },
  },
  {
    id: "bts-capacity", topic: "cycle", dataflow: "DF_BTS",
    nameKo: "제조업 설비가동률", nameEn: "Manufacturing capacity utilisation",
    unitKo: "%", freq: "Q", headline: false,
    selector: { MEASURE: "CURT", ACTIVITY: "C", TIME_HORIZ: "C", ADJUSTMENT: "Y", FREQ: "Q" },
  },
  {
    id: "bts-retail", topic: "cycle", dataflow: "DF_BTS",
    nameKo: "소매업 경기", nameEn: "Retail trade confidence",
    unitKo: "% 포인트(응답 차이)", freq: "M", headline: false,
    selector: { MEASURE: "BCICP", ACTIVITY: "G47", ADJUSTMENT: "Y", FREQ: "M" },
  },
  {
    id: "cs-economic", topic: "cycle", dataflow: "DF_CS",
    nameKo: "소비자 경제상황 전망", nameEn: "Consumer economic outlook",
    unitKo: "% 포인트(응답 차이)", freq: "M", headline: false,
    selector: { MEASURE: "ES", TIME_HORIZ: "FT", ADJUSTMENT: "Y", FREQ: "M" },
  },
  {
    id: "cs-prices", topic: "cycle", dataflow: "DF_CS",
    nameKo: "소비자 물가 전망", nameEn: "Consumer price expectations",
    unitKo: "% 포인트(응답 차이)", freq: "M", headline: false,
    selector: { MEASURE: "IN", TIME_HORIZ: "FT", ADJUSTMENT: "Y", FREQ: "M" },
  },

  // ── 생산 ──────────────────────────────────────────────────────────────────
  {
    id: "industrial-production", topic: "output", dataflow: "DF_INDSERV",
    nameKo: "산업생산", nameEn: "Industrial production",
    unitKo: "지수", freq: "M", headline: false,
    selector: { MEASURE: "PRVM", ACTIVITY: "BTE", ADJUSTMENT: "Y", UNIT_MEASURE: "IX", FREQ: "M" },
  },
  {
    id: "manufacturing-production", topic: "output", dataflow: "DF_INDSERV",
    nameKo: "제조업 생산", nameEn: "Manufacturing production",
    unitKo: "지수", freq: "M", headline: false,
    selector: { MEASURE: "PRVM", ACTIVITY: "C", ADJUSTMENT: "Y", UNIT_MEASURE: "IX", FREQ: "M" },
  },
  {
    id: "construction", topic: "output", dataflow: "DF_INDSERV",
    nameKo: "건설 생산", nameEn: "Construction output",
    unitKo: "지수", freq: "M", headline: false,
    selector: { MEASURE: "PRVM", ACTIVITY: "F", ADJUSTMENT: "Y", UNIT_MEASURE: "IX", FREQ: "M" },
  },
  {
    id: "retail-volume", topic: "output", dataflow: "DF_INDSERV",
    nameKo: "소매판매", nameEn: "Retail trade volume",
    unitKo: "지수", freq: "M", headline: false,
    selector: { MEASURE: "TOVM", ACTIVITY: "G47", ADJUSTMENT: "Y", UNIT_MEASURE: "IX", FREQ: "M" },
  },
  {
    id: "car-registrations", topic: "output", dataflow: "DF_INDSERV",
    nameKo: "승용차 등록대수", nameEn: "Passenger car registrations",
    unitKo: "대", freq: "M", headline: false,
    selector: { MEASURE: "TOCAPA", ADJUSTMENT: "Y", UNIT_MEASURE: "VEH", FREQ: "M" },
  },
  {
    id: "dwelling-permits", topic: "output", dataflow: "DF_INDSERV",
    nameKo: "주택 건축허가", nameEn: "Permits issued for dwellings",
    unitKo: "지수", freq: "M", headline: false,
    selector: { MEASURE: "NODW", ACTIVITY: "F41", ADJUSTMENT: "Y", FREQ: "M" },
  },

  // ── 금리·금융 ─────────────────────────────────────────────────────────────
  {
    id: "short-rate", topic: "finance", dataflow: "DF_FINMARK",
    nameKo: "단기금리(3개월)", nameEn: "Short-term interest rate (3-month)",
    unitKo: "% (연율)", freq: "M", headline: true,
    selector: { MEASURE: "IR3TIB", UNIT_MEASURE: "PA", FREQ: "M" },
  },
  {
    id: "long-rate", topic: "finance", dataflow: "DF_FINMARK",
    nameKo: "장기국채금리(10년)", nameEn: "Long-term interest rate (10-year)",
    unitKo: "% (연율)", freq: "M", headline: true,
    selector: { MEASURE: "IRLT", UNIT_MEASURE: "PA", FREQ: "M" },
  },
  {
    id: "call-rate", topic: "finance", dataflow: "DF_FINMARK",
    nameKo: "초단기금리(콜)", nameEn: "Immediate interest rate (call money)",
    unitKo: "% (연율)", freq: "M", headline: false,
    selector: { MEASURE: "IRSTCI", UNIT_MEASURE: "PA", FREQ: "M" },
  },
  {
    id: "share-prices", topic: "finance", dataflow: "DF_FINMARK",
    nameKo: "주가지수", nameEn: "Share prices",
    unitKo: "지수", freq: "M", headline: false,
    selector: { MEASURE: "SHARE", UNIT_MEASURE: "IX", FREQ: "M" },
  },
  {
    id: "exchange-rate", topic: "finance", dataflow: "DF_FINMARK",
    nameKo: "명목환율(미 달러당)", nameEn: "Nominal exchange rate (per US dollar)",
    unitKo: "자국통화 / 미 달러", freq: "M", headline: false,
    selector: { MEASURE: "CC", UNIT_MEASURE: "XDC_USD", FREQ: "M" },
  },
  {
    id: "reer", topic: "finance", dataflow: "DF_FINMARK",
    nameKo: "실질실효환율(물가 기준)", nameEn: "Real effective exchange rate (CPI based)",
    unitKo: "지수", freq: "M", headline: false,
    selector: { MEASURE: "CCRE", UNIT_MEASURE: "IX", FREQ: "M" },
  },
  {
    id: "money-m3", topic: "finance", dataflow: "DF_KEI",
    nameKo: "광의통화(M3) 증가율", nameEn: "Broad money (M3) growth",
    unitKo: "% (전년 동월 대비)", freq: "M", headline: false,
    selector: { MEASURE: "MABM", UNIT_MEASURE: "GR", TRANSFORMATION: "GY", FREQ: "M" },
  },

  // ── 무역 ──────────────────────────────────────────────────────────────────
  {
    id: "exports", topic: "trade", dataflow: "DF_IMTS",
    nameKo: "수출", nameEn: "Merchandise exports",
    unitKo: "미 달러", freq: "M", headline: false,
    selector: { TRADE_FLOW: "X", COUNTERPART_AREA: "W", PRODUCT_TYPE: "C", UNIT_MEASURE: "USD_EXC", ADJUSTMENT: "Y", TRANSFORMATION: "N", FREQ: "M" },
  },
  {
    id: "imports", topic: "trade", dataflow: "DF_IMTS",
    nameKo: "수입", nameEn: "Merchandise imports",
    unitKo: "미 달러", freq: "M", headline: false,
    selector: { TRADE_FLOW: "M", COUNTERPART_AREA: "W", PRODUCT_TYPE: "C", UNIT_MEASURE: "USD_EXC", ADJUSTMENT: "Y", TRANSFORMATION: "N", FREQ: "M" },
  },
  {
    id: "trade-balance", topic: "trade", dataflow: "DF_IMTS",
    nameKo: "무역수지", nameEn: "Merchandise trade balance",
    unitKo: "미 달러", freq: "M", headline: false,
    selector: { TRADE_FLOW: "TB", COUNTERPART_AREA: "W", PRODUCT_TYPE: "C", UNIT_MEASURE: "USD_EXC", ADJUSTMENT: "Y", TRANSFORMATION: "N", FREQ: "M" },
  },
  {
    id: "exports-growth", topic: "trade", dataflow: "DF_IMTS",
    nameKo: "수출 증가율", nameEn: "Merchandise export growth (YoY)",
    unitKo: "% (전년 동월 대비)", freq: "M", headline: false,
    selector: { TRADE_FLOW: "X", COUNTERPART_AREA: "W", PRODUCT_TYPE: "C", UNIT_MEASURE: "PC", ADJUSTMENT: "Y", TRANSFORMATION: "GY", FREQ: "M" },
  },
  {
    id: "current-account", topic: "trade", dataflow: "DF_BOP",
    nameKo: "경상수지(GDP 대비)", nameEn: "Current account balance (% of GDP)",
    unitKo: "% (GDP 대비)", freq: "Q", headline: false,
    selector: { MEASURE: "CA", ACCOUNTING_ENTRY: "B", FS_ENTRY: "T", UNIT_MEASURE: "PT_B1GQ", ADJUSTMENT: "Y", COUNTERPART_AREA: "WXD", FREQ: "Q" },
  },
  {
    // GDP 대비(PT_B1GQ)는 경상수지에만 있다. 상품·서비스 수지는 달러 금액으로만 발행된다.
    id: "goods-balance", topic: "trade", dataflow: "DF_BOP",
    nameKo: "상품수지", nameEn: "Goods balance",
    unitKo: "미 달러", freq: "Q", headline: false,
    selector: { MEASURE: "G", ACCOUNTING_ENTRY: "B", FS_ENTRY: "T", UNIT_MEASURE: "USD_EXC", ADJUSTMENT: "Y", COUNTERPART_AREA: "WXD", FREQ: "Q" },
  },
  {
    id: "services-balance", topic: "trade", dataflow: "DF_BOP",
    nameKo: "서비스수지", nameEn: "Services balance",
    unitKo: "미 달러", freq: "Q", headline: false,
    selector: { MEASURE: "S", ACCOUNTING_ENTRY: "B", FS_ENTRY: "T", UNIT_MEASURE: "USD_EXC", ADJUSTMENT: "Y", COUNTERPART_AREA: "WXD", FREQ: "Q" },
  },

  // ── 성장 ──────────────────────────────────────────────────────────────────
  {
    id: "gdp-growth", topic: "growth", dataflow: "DF_KEI",
    nameKo: "GDP 성장률(전기비)", nameEn: "GDP growth (QoQ)",
    unitKo: "% (전기 대비)", freq: "Q", headline: true,
    selector: { MEASURE: "B1GQ_Q", UNIT_MEASURE: "GR", TRANSFORMATION: "G1", FREQ: "Q" },
  },
  {
    id: "gdp-growth-yoy", topic: "growth", dataflow: "DF_KEI",
    nameKo: "GDP 성장률(전년 동기비)", nameEn: "GDP growth (YoY)",
    unitKo: "% (전년 동기 대비)", freq: "Q", headline: false,
    selector: { MEASURE: "B1GQ_Q", UNIT_MEASURE: "GR", TRANSFORMATION: "GY", FREQ: "Q" },
  },
  {
    id: "household-consumption", topic: "growth", dataflow: "DF_KEI",
    nameKo: "민간소비 증가율", nameEn: "Household consumption growth (YoY)",
    unitKo: "% (전년 동기 대비)", freq: "Q", headline: false,
    selector: { MEASURE: "P3_S1M_Q", UNIT_MEASURE: "GR", TRANSFORMATION: "GY", FREQ: "Q" },
  },
  {
    id: "investment", topic: "growth", dataflow: "DF_KEI",
    nameKo: "총고정자본형성 증가율", nameEn: "Gross fixed capital formation growth (YoY)",
    unitKo: "% (전년 동기 대비)", freq: "Q", headline: false,
    selector: { MEASURE: "P51G_Q", UNIT_MEASURE: "GR", TRANSFORMATION: "GY", FREQ: "Q" },
  },
  {
    id: "exports-volume", topic: "growth", dataflow: "DF_KEI",
    nameKo: "재화·서비스 수출 증가율", nameEn: "Exports of goods and services growth (YoY)",
    unitKo: "% (전년 동기 대비)", freq: "Q", headline: false,
    selector: { MEASURE: "P6_Q", UNIT_MEASURE: "GR", TRANSFORMATION: "GY", FREQ: "Q" },
  },
  {
    id: "imports-volume", topic: "growth", dataflow: "DF_KEI",
    nameKo: "재화·서비스 수입 증가율", nameEn: "Imports of goods and services growth (YoY)",
    unitKo: "% (전년 동기 대비)", freq: "Q", headline: false,
    selector: { MEASURE: "P7_Q", UNIT_MEASURE: "GR", TRANSFORMATION: "GY", FREQ: "Q" },
  },
];

// 출처는 여기서 한 번만 붙인다. ECOS·FRED를 더할 때는 같은 모양의 배열을 만들어
// source만 다르게 두고 아래에 이어 붙이면 된다.
export const INDICATORS = OECD_INDICATORS.map((indicator) => ({ source: SOURCE_OECD, ...indicator }));

// OECD 회원국 + 자주 보는 집계. 국가명 한글은 응답에 없어서 여기서만 관리한다.
// 여기 없는 코드는 카탈로그에서 빠지고, 배치가 몇 개를 건너뛰었는지 이름과 함께 찍는다 —
// 기계번역으로 한글명을 지어내지 않는다.
export const COUNTRIES = {
  KOR: "대한민국", USA: "미국", JPN: "일본", DEU: "독일", FRA: "프랑스", GBR: "영국",
  ITA: "이탈리아", CAN: "캐나다", AUS: "호주", NZL: "뉴질랜드", ESP: "스페인",
  NLD: "네덜란드", BEL: "벨기에", AUT: "오스트리아", CHE: "스위스", SWE: "스웨덴",
  NOR: "노르웨이", DNK: "덴마크", FIN: "핀란드", ISL: "아이슬란드", IRL: "아일랜드",
  PRT: "포르투갈", GRC: "그리스", LUX: "룩셈부르크", POL: "폴란드", CZE: "체코",
  SVK: "슬로바키아", HUN: "헝가리", SVN: "슬로베니아", EST: "에스토니아",
  LVA: "라트비아", LTU: "리투아니아", TUR: "튀르키예", MEX: "멕시코", CHL: "칠레",
  COL: "콜롬비아", CRI: "코스타리카", ISR: "이스라엘",
  // 비회원 주요국. 지표마다 포함 범위가 달라 일부만 값이 있다.
  CHN: "중국", IND: "인도", BRA: "브라질", ZAF: "남아프리카공화국", IDN: "인도네시아",
  RUS: "러시아", SAU: "사우디아라비아", ARG: "아르헨티나", BGR: "불가리아",
  HRV: "크로아티아", ROU: "루마니아", CYP: "키프로스", MLT: "몰타",
  OECD: "OECD 전체", EA20: "유로존", EA19: "유로존(19개국)", EU27_2020: "유럽연합(27개국)",
  G7: "주요 7개국(G7)", G20: "주요 20개국(G20)",
};

// 홈 카드에 올릴 소수의 지표. 카드 높이를 지켜야 하므로 의도적으로 적게 둔다.
// headline 플래그가 후보를 정하고, 어느 국가를 보여줄지는 여기서 정한다.
export const HOME_HEADLINES = [
  { indicator: "cli", country: "KOR" },
  { indicator: "cpi", country: "KOR" },
  { indicator: "unemployment", country: "KOR" },
  { indicator: "long-rate", country: "KOR" },
];

export function matchesSelector(row, selector) {
  return Object.entries(selector).every(([dimension, value]) => row[dimension] === value);
}
