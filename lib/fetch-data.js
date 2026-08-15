// lib/fetch-data.js
// 배치가 만든 데이터 파일을 받는 한 곳. 캐시 정책과 "캐시에서 왔는지"를 여기서 정한다.
//
// ── 왜 필요했나 ────────────────────────────────────────────────────────────
// 배치는 커밋했고 Pages도 배포했는데 브라우저가 어제 파일을 들고 있었다. 실측으로
// 같은 경로에 그냥 요청하면 asOf 2026-08-12, 캐시 무력화 질의를 붙이면 2026-08-13이
// 왔다(저장소와 서버는 둘 다 08-13). 화면에는 배치가 고장 난 것과 똑같이 보인다 —
// 실제로 며칠을 파이프라인 쪽에서 헤맸다.
//
// 코드 파일은 이미 `?v=20260814-1` 같은 표식을 달고 있었는데, 정작 매일 바뀌는
// 데이터 파일에는 아무것도 없었다. 드물게 바뀌는 것에는 버전이 있고 매일 바뀌는
// 것에는 없었다.
//
// ── 왜 ?v= 나 ?t=Date.now() 가 아닌가 ──────────────────────────────────────
// 빌드 스텝이 없는 정적 사이트이고, 데이터는 코드 변경 없이 바뀐다. 그래서 코드에
// 박아 두는 버전 문자열로는 데이터 갱신을 따라갈 수 없다(배치가 HTML을 고치지 않는다).
// `?t=Date.now()`는 캐시를 확실히 비껴가지만 대가가 크다 — 매 로드가 전량 재다운로드가
// 되고, CDN 입장에서는 매번 다른 URL이라 캐시가 아예 서지 않는다.
//
// 대신 `cache: "no-cache"`를 쓴다. 캐시를 끄는 게 아니라 **매번 서버에 물어보게**
// 하는 것이다. 내용이 그대로면 304가 오고 본문은 다시 받지 않는다. 바뀌었을 때만
// 새 본문이 온다. 빌드 스텝도, URL 조작도 필요 없다.
//
// 종목 목록(data/tickers-*.json)은 예외다. 주 1회 바뀌고 합쳐서 4MB가 넘어,
// 매 로드마다 조건부 요청을 세 번 더 보낼 이유가 없다. 기본 캐시를 그대로 쓴다.
(function (global) {
  "use strict";

  // 마지막 요청이 캐시에서 왔는지 기록한다. 화면이 "지금 받은 값"과 "들고 있던 값"을
  // 구별해 말할 수 있어야 하기 때문이다.
  var lastLoad = {};

  // Resource Timing으로 실제 전송량을 본다. 같은 출처라 값이 노출된다.
  //   transferSize 0      캐시에서 그대로 나온 것(네트워크를 타지 않음)
  //   transferSize 작음   조건부 요청 후 304 — 헤더만 오갔다
  //   transferSize 큼     새 본문을 받았다
  function measure(url) {
    try {
      var entries = performance.getEntriesByName(String(url), "resource");
      var entry = entries[entries.length - 1];
      if (!entry) return { known: false };
      return {
        known: true,
        transferSize: entry.transferSize,
        fromCache: entry.transferSize === 0,
        revalidated: entry.transferSize > 0 && entry.encodedBodySize === 0,
      };
    } catch (error) {
      return { known: false };
    }
  }

  // options.fresh === false 면 기본 캐시를 쓴다(주 단위로 바뀌는 큰 파일).
  function json(path, options) {
    var opts = options || {};
    var fresh = opts.fresh !== false;
    var url = new URL(path, document.baseURI);
    var init = fresh ? { cache: "no-cache" } : {};
    return fetch(url, init).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json().then(function (payload) {
        lastLoad[path] = Object.assign(
          { at: new Date().toISOString(), status: response.status, fresh: fresh },
          measure(url)
        );
        return payload;
      });
    });
  }

  function loadInfo(path) {
    return lastLoad[path] || null;
  }

  // 화면이 "이 값이 캐시에서 나온 것"이라고 말해야 할 때 쓴다. 조건부 요청까지 갔다면
  // 서버가 그대로라고 답한 것이므로 낡은 게 아니다 — 캐시에서만 나온 경우만 참이다.
  function servedFromCache(path) {
    var info = lastLoad[path];
    return Boolean(info && info.known && info.fromCache);
  }

  global.DataFetch = { json: json, loadInfo: loadInfo, servedFromCache: servedFromCache };
})(window);
