"""AI API 호출 (REST, 표준 라이브러리 urllib만 사용).

지원: Gemini `generateContent`, Claude Messages API.
요청 구성 -> HTTP POST -> 응답 JSON에서 텍스트 추출 -> 오류는 원인을 담아 AIError로.
"""
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

DEFAULT_MODELS = {"gemini": "gemini-3.6-flash", "claude": "claude-opus-5-5"}
DEFAULT_TEMPERATURE = 0.3   # 낮을수록 일관된(형식을 잘 지키는) 출력
DEFAULT_MAX_TOKENS = 4096   # 생각(thinking) 토큰도 이 예산에 포함된다
KEY_ENV = {"gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY"}


class AIError(Exception):
    pass


@dataclass
class AIResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    truncated: bool = False     # max_tokens에 걸려 잘렸는지


def resolve_provider(explicit=None, env=None):
    """provider와 API Key를 환경변수에서 정한다. 키가 없으면 key는 빈 문자열."""
    env = os.environ if env is None else env
    generic = env.get("AI_API_KEY", "").strip()
    provider = (explicit or env.get("AI_PROVIDER", "")).strip().lower()
    if provider and provider not in KEY_ENV:
        raise AIError(f"지원하지 않는 provider입니다: {provider} (gemini | claude)")
    if not provider:
        if generic:
            provider = "claude" if generic.startswith("sk-ant-") else "gemini"
        elif env.get("ANTHROPIC_API_KEY", "").strip():
            provider = "claude"
        else:
            provider = "gemini"
    key = generic or env.get(KEY_ENV[provider], "").strip()
    return provider, key


HTTP_HINT = {
    400: "요청 형식 오류 (모델 이름이나 파라미터 값을 확인하세요)",
    401: "인증 실패 (API Key가 올바른지 확인하세요)",
    403: "권한 없음 (API Key 권한 또는 결제 설정을 확인하세요)",
    404: "모델 또는 엔드포인트를 찾을 수 없음 (--model 값을 확인하세요)",
    429: "요청 한도 초과 (잠시 후 다시 시도하세요)",
}


def _post(url, headers, body, timeout):
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST", headers={"content-type": "application/json", **headers}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            payload = json.loads(e.read().decode("utf-8", errors="replace"))
            detail = (payload.get("error") or {}).get("message", "")
        except Exception:
            pass
        hint = HTTP_HINT.get(e.code, "서버 오류 (잠시 후 다시 시도하세요)" if e.code >= 500 else "요청 실패")
        raise AIError(f"HTTP {e.code} {hint}" + (f" — {detail}" if detail else ""))
    except (socket.timeout, TimeoutError):
        raise AIError(f"응답 시간 초과 ({timeout}초). 네트워크 상태를 확인하거나 --timeout 을 늘리세요.")
    except urllib.error.URLError as e:
        raise AIError(f"네트워크 오류 (API 서버에 연결할 수 없음): {e.reason}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise AIError("API 응답을 JSON으로 해석할 수 없습니다.")


def _call_gemini(model, key, system, user, temperature, max_tokens, timeout):
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(model, safe='')}:generateContent"
    )
    generation = {
        "temperature": DEFAULT_TEMPERATURE if temperature is None else temperature,
        "maxOutputTokens": max_tokens,
        "responseMimeType": "application/json",
    }
    if model.startswith("gemini-3"):
        # 생각 토큰이 출력 예산을 다 써 버리지 않도록 낮게 둔다.
        generation["thinkingConfig"] = {"thinkingLevel": os.environ.get("GEMINI_THINKING", "low")}
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": generation,
    }
    data = _post(url, {"x-goog-api-key": key}, body, timeout)
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "알 수 없음")
        raise AIError(f"모델이 응답을 생성하지 않았습니다 (차단 사유: {reason}).")
    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "\n".join(p["text"] for p in parts if p.get("text") and not p.get("thought"))
    usage = data.get("usageMetadata") or {}
    return AIResult(
        text=text,
        input_tokens=usage.get("promptTokenCount", 0),
        output_tokens=usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0),
        truncated=candidate.get("finishReason") == "MAX_TOKENS",
    )


def _call_claude(model, key, system, user, temperature, max_tokens, timeout):
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    # 최신 Claude 모델은 temperature를 받지 않는다(400). 사용자가 명시했을 때만 보낸다.
    if temperature is not None:
        body["temperature"] = temperature
    data = _post(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
        body,
        timeout,
    )
    if data.get("stop_reason") == "refusal":
        raise AIError("모델이 이 요청의 처리를 거절했습니다 (stop_reason: refusal).")
    text = "\n".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
    usage = data.get("usage") or {}
    return AIResult(
        text=text,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        truncated=data.get("stop_reason") == "max_tokens",
    )


def generate(provider, model, key, system, user, temperature=None,
             max_tokens=DEFAULT_MAX_TOKENS, timeout=90):
    """AI API를 정확히 1회 호출한다."""
    call = _call_claude if provider == "claude" else _call_gemini
    result = call(model, key, system, user, temperature, max_tokens, timeout)
    if not result.text.strip():
        hint = " --max-tokens 값을 늘려 보세요." if result.truncated else ""
        raise AIError("모델 응답에 텍스트가 없습니다." + hint)
    return result
