// lib/session.js
// localStorage 세션 읽기의 단일 출처. 자산 입력 화면과 홈 화면이 함께 쓴다.
//
// 여기 있는 규칙은 하나다: 사용자가 입력한 자산을 우리 판단으로 지우지 않는다.
// 예전에는 스키마 번호가 다르면 곧바로 빈 세션을 덮어썼다. 앱을 업데이트해
// SESSION_SCHEMA가 올라가는 순간, 자산 20건을 입력해 둔 사용자의 기록이 실제로
// 삭제되고 화면에는 "자산이 없어요"가 뜬다 — 데이터가 살아 있지도 않고, 사라졌다는
// 사실조차 알려주지 않는다.
(function (global) {
  "use strict";

  var KEY = "assetInput.session";
  var SCHEMA = 7;

  function backupKey(schema) {
    return KEY + ".backup.v" + (schema == null ? "unknown" : schema);
  }

  function readRaw() {
    try {
      var stored = localStorage.getItem(KEY);
      if (!stored) return null;
      var parsed = JSON.parse(stored);
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch (error) {
      console.error("[세션] 읽기 실패:", error);
      return null;
    }
  }

  function usableAssets(raw) {
    if (!raw || !Array.isArray(raw.assets)) return [];
    return raw.assets.filter(function (asset) {
      return asset && asset.group && asset.fields && typeof asset.fields === "object";
    });
  }

  // 스키마가 달라도 자산 레코드의 모양({group, fields, autoFields})은 그대로인 경우가
  // 대부분이다. 그 모양이 유지되는 한 그대로 살려 쓴다 — 되살릴 수 있는 걸 버리지 않는다.
  function classify(raw) {
    if (!raw) return { status: "empty", assets: [], schema: null };
    var assets = usableAssets(raw);
    if (raw.schema === SCHEMA) return { status: "ok", assets: assets, schema: raw.schema };
    if (assets.length) return { status: "migrated", assets: assets, schema: raw.schema };
    return { status: "legacy-empty", assets: [], schema: raw.schema };
  }

  function read() {
    return classify(readRaw());
  }

  // 원본을 덮어쓰기 전에 반드시 부른다. 실패해도(용량 초과 등) 예외를 밖으로 던지지
  // 않되, 백업이 실패했다는 사실은 남긴다.
  function backupLegacy(raw) {
    if (!raw) return false;
    try {
      localStorage.setItem(backupKey(raw.schema), JSON.stringify(raw));
      return true;
    } catch (error) {
      console.error("[세션] 예전 형식 백업 실패:", error);
      return false;
    }
  }

  global.SessionStore = {
    KEY: KEY,
    SCHEMA: SCHEMA,
    backupKey: backupKey,
    readRaw: readRaw,
    classify: classify,
    read: read,
    backupLegacy: backupLegacy,
  };
})(window);
