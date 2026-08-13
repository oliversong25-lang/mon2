// scripts/lib/http.mjs
// 배치의 모든 외부 호출이 지나는 자리. 두 가지만 한다: 어디에 실패했는지 이름을 남기고,
// 일시적인 네트워크 오류는 몇 번 다시 시도한다.
//
// 왜 필요했나: 2026-08-12 예약 실행이 `[시세 배치 실패] fetch failed` 한 줄만 남기고
// 죽었다. undici가 던지는 이 메시지에는 URL이 없어서 어느 API가 끊긴 건지 로그만으로는
// 알 수 없었고, 재시도가 없어 한 번의 순간적인 실패가 그날 데이터를 통째로 날렸다.

// 재시도해도 소용없는 것과 있는 것을 가른다. 4xx는 우리가 잘못 부른 것이므로 즉시
// 포기하고, 네트워크 오류와 5xx·429는 잠깐 뒤 다시 시도한다.
function isRetriable(error) {
  if (error && error.status >= 400 && error.status < 500 && error.status !== 429) return false;
  return true;
}

export class HttpError extends Error {
  constructor(message, { label, url, status, cause } = {}) {
    super(message);
    this.name = "HttpError";
    this.label = label;
    this.url = url;
    this.status = status;
    if (cause) this.cause = cause;
  }
}

// URL에는 인증키가 붙어 있다. 로그에 그대로 찍으면 공개 저장소의 Actions 로그로
// 새어 나가므로, 어디를 불렀는지는 남기되 키 값은 가린다.
export function redactUrl(url) {
  try {
    const parsed = new URL(String(url));
    const secrets = ["serviceKey", "authkey", "crtfc_key", "apikey", "api_key", "key"];
    parsed.searchParams.forEach((_value, name) => {
      if (secrets.some((secret) => secret.toLowerCase() === name.toLowerCase())) parsed.searchParams.set(name, "***");
    });
    return parsed.toString();
  } catch {
    return String(url);
  }
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

// label은 사람이 읽을 API 이름이다("금융위원회_주식시세정보", "CoinGecko" 등).
// 실패 메시지는 항상 `[label] ... (URL)` 형태가 되므로 로그만 보고 어디가 끊겼는지 안다.
export async function fetchWithRetry(label, url, options = {}, { attempts = 3, baseDelayMs = 800, fetchImpl = fetch } = {}) {
  const safeUrl = redactUrl(url);
  let lastError = null;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await fetchImpl(url, options);
      if (!response.ok) {
        const error = new HttpError(`[${label}] HTTP ${response.status} (${safeUrl})`, { label, url: safeUrl, status: response.status });
        if (!isRetriable(error) || attempt === attempts) throw error;
        lastError = error;
      } else {
        return response;
      }
    } catch (error) {
      if (error instanceof HttpError && !isRetriable(error)) throw error;
      // undici의 "fetch failed"는 원인이 cause에만 들어 있다. 그대로 두면 로그에
      // 아무 단서도 남지 않으므로 label·URL과 함께 원인을 풀어서 붙인다.
      const reason = error instanceof HttpError ? error.message : `${error.message}${error.cause ? ` (${error.cause.code || error.cause.message})` : ""}`;
      lastError = error instanceof HttpError ? error : new HttpError(`[${label}] ${reason} (${safeUrl})`, { label, url: safeUrl, cause: error });
      if (attempt === attempts) break;
      const delay = baseDelayMs * 2 ** (attempt - 1);
      // 중간 시도 로그에도 URL을 남긴다. 마지막 예외에만 붙이면, 재시도 끝에 성공한
      // 날에는 어디가 불안정했는지가 기록에서 사라진다.
      console.warn(`[${label}] ${attempt}차 시도 실패 (${safeUrl}) — ${Math.round(delay / 100) / 10}초 후 재시도: ${reason}`);
      await sleep(delay);
    }
  }

  throw lastError;
}
