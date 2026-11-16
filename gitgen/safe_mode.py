"""AI에 보내기 전 diff 가공: 민감정보 마스킹, 민감 파일 제외, 파일/줄 수 제한."""
import fnmatch
import re
from dataclasses import dataclass, field

SAFE_MAX_FILES = 10       # safe-mode에서 전송할 최대 파일 수
SAFE_MAX_LINES = 200      # safe-mode에서 전송할 최대 diff 줄 수
NORMAL_MAX_LINES = 1500   # safe-mode가 아닐 때도 비용 폭주를 막는 상한

# (정규표현식, 치환문) — 구체적인 패턴이 먼저 온다.
MASK_RULES = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", "[MASKED_PRIVATE_KEY]"),
    (r"\bsk-[A-Za-z0-9_\-]{20,}", "[MASKED_API_KEY]"),
    (r"\bAIza[0-9A-Za-z_\-]{35}", "[MASKED_API_KEY]"),
    (r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "[MASKED_API_KEY]"),
    (r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})", "[MASKED_TOKEN]"),
    (r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=\-]{20,}", r"\1[MASKED_TOKEN]"),
    # password = "값" 처럼 따옴표로 감싼 비밀 값 (변수 이름은 남긴다)
    (r"(?i)\b([\w.\-]*(?:password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key)[\w\-]*"
     r"[\"']?\s*[:=]\s*)([\"'])(?!\[MASKED)[^\"'\s]{6,}\2", r"\1\2[MASKED_SECRET]\2"),
    # .env 형식: API_KEY=값
    (r"(?m)^([+\-]?\s*(?:export\s+)?[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*=)(?!\[MASKED)\S{6,}$",
     r"\1[MASKED_SECRET]"),
    (r"\b[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+\b", "[MASKED_EMAIL]"),
    (r"\b\d{6}-[1-4]\d{6}\b", "[MASKED_ID_NUMBER]"),
    (r"\b01[016789]-?\d{3,4}-?\d{4}\b", "[MASKED_PHONE]"),
]

# 내용 자체를 보내지 않는 파일 (이름만 알려 준다)
SENSITIVE_FILES = [".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa", "id_ed25519",
                   "credentials", "credentials.json", "secrets.*"]
SENSITIVE_ALLOW = [".env.example", ".env.sample"]


@dataclass
class Prepared:
    text: str = ""
    total_files: int = 0
    sent_files: int = 0
    total_lines: int = 0
    sent_lines: int = 0
    masked: int = 0                                 # 마스킹한 횟수
    excluded: list = field(default_factory=list)    # 민감 파일이라 내용 제외
    omitted: list = field(default_factory=list)     # 파일/줄 수 제한으로 제외


def split_diff(diff):
    """diff 텍스트를 파일 단위 [(경로, 줄 목록)]으로 나눈다."""
    blocks = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/.+? b/(.+)$", line)
            blocks.append((match.group(1) if match else "?", [line]))
        elif blocks:
            blocks[-1][1].append(line)
    return blocks


def is_sensitive_file(path):
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if any(fnmatch.fnmatch(name, g) for g in SENSITIVE_ALLOW):
        return False
    return any(fnmatch.fnmatch(name, g) for g in SENSITIVE_FILES)


def mask_text(text):
    """민감정보 패턴을 치환하고 (결과, 치환 횟수)를 돌려준다."""
    total = 0
    for pattern, replacement in MASK_RULES:
        text, n = re.subn(pattern, replacement, text)
        total += n
    return text, total


def prepare_diff(diff, safe=False):
    """AI에 보낼 diff를 만든다.

    safe=True : 민감 파일 내용 제외 + 패턴 마스킹 + 최대 10개 파일 / 200줄.
    safe=False: 최대 1500줄 상한만 적용한다.
    """
    blocks = split_diff(diff)
    out = Prepared(total_files=len(blocks), total_lines=len(diff.splitlines()))
    max_lines = SAFE_MAX_LINES if safe else NORMAL_MAX_LINES

    if safe and len(blocks) > SAFE_MAX_FILES:
        out.omitted = [path for path, _ in blocks[SAFE_MAX_FILES:]]
        blocks = blocks[:SAFE_MAX_FILES]
    if safe:
        for i, (path, lines) in enumerate(blocks):
            if is_sensitive_file(path):
                out.excluded.append(path)
                blocks[i] = (path, [lines[0], "[safe-mode] 민감 파일로 분류되어 내용을 전송하지 않습니다."])

    parts = []
    remaining = max_lines
    for path, lines in blocks:
        if remaining <= 0:                  # 줄 수 예산을 다 썼으면 파일 이름만 알린다
            out.omitted.append(path)
            continue
        kept = lines[:remaining]
        remaining -= len(kept)
        out.sent_lines += len(kept)
        if len(lines) > len(kept):
            kept.append(f"... (이 파일의 나머지 {len(lines) - len(kept)}줄 생략)")
        parts.append("\n".join(kept))
    out.sent_files = len(parts)
    if out.omitted:
        parts.append("[전송 제한으로 내용이 생략된 파일]\n" + "\n".join(f"- {p}" for p in out.omitted))

    out.text = "\n".join(parts)
    if safe:
        out.text, out.masked = mask_text(out.text)
    return out
