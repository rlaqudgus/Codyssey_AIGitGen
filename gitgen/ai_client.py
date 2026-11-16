"""AI API 호출 (Gemini generateContent, REST). 표준 라이브러리 urllib만 사용한다.

요청 구성 -> HTTP POST -> 응답 JSON에서 텍스트 추출 -> 오류는 원인을 담아 AIError로.
"""
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_TEMPERATURE = 0.3   # 낮을수록 일관된(형식을 잘 지키는) 출력
DEFAULT_MAX_TOKENS = 4096   # 생각(thinking) 토큰도 이 예산에 포함된다
TIMEOUT = 90                # 응답 대기 시간(초)


class AIError(Exception):
    pass


@dataclass
class AIResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    truncated: bool = False     # max_tokens에 걸려 잘렸는지


HTTP_HINT = {
    400: "요청 형식 오류 (모델 이름이나 파라미터 값을 확인하세요)",
    401: "인증 실패 (API Key가 올바른지 확인하세요)",
    403: "권한 없음 (API Key 권한을 확인하세요)",
    404: "모델을 찾을 수 없음 (--model 값을 확인하세요)",
    429: "요청 한도 초과 (잠시 후 다시 시도하세요)",
}


def _post(url, headers, body):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"content-type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            payload = json.loads(e.read().decode("utf-8", errors="replace"))
            detail = (payload.get("error") or {}).get("message", "")
        except Exception:
            pass
        hint = HTTP_HINT.get(e.code, "서버 오류 (잠시 후 다시 시도하세요)" if e.code >= 500 else "요청 실패")
        # Gemini는 잘못된 키에 401이 아니라 400을 돌려준다. 원인에 맞는 안내로 바꾼다.
        if e.code == 400 and "api key" in detail.lower():
            hint = HTTP_HINT[401]
        raise AIError(f"HTTP {e.code} {hint}" + (f" — {detail}" if detail else ""))
    except (socket.timeout, TimeoutError):
        raise AIError(f"응답 시간 초과 ({TIMEOUT}초). 네트워크 상태를 확인하세요.")
    except urllib.error.URLError as e:
        raise AIError(f"네트워크 오류 (API 서버에 연결할 수 없음): {e.reason}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise AIError("API 응답을 JSON으로 해석할 수 없습니다.")


def generate(model, key, system, user, temperature=DEFAULT_TEMPERATURE, max_tokens=DEFAULT_MAX_TOKENS):
    """AI API를 정확히 1회 호출해 모델이 쓴 텍스트를 돌려준다."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(model, safe='')}:generateContent"
    )
    generation = {
        "temperature": temperature,
        "maxOutputTokens": max_tokens,
        "responseMimeType": "application/json",
    }
    if model.startswith("gemini-3"):
        # 생각 토큰이 출력 예산을 다 써 버리지 않도록 낮게 둔다.
        generation["thinkingConfig"] = {"thinkingLevel": "low"}
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": generation,
    }
    data = _post(url, {"x-goog-api-key": key}, body)

    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "알 수 없음")
        raise AIError(f"모델이 응답을 생성하지 않았습니다 (차단 사유: {reason}).")
    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "\n".join(p["text"] for p in parts if p.get("text") and not p.get("thought"))
    truncated = candidate.get("finishReason") == "MAX_TOKENS"
    if not text.strip():
        raise AIError("모델 응답에 텍스트가 없습니다." + (" --max-tokens 값을 늘려 보세요." if truncated else ""))
    usage = data.get("usageMetadata") or {}
    return AIResult(
        text=text,
        input_tokens=usage.get("promptTokenCount", 0),
        output_tokens=usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0),
        truncated=truncated,
    )
