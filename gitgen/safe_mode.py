"""AI에 보내기 전 diff 가공: 민감정보 마스킹, 민감 파일 제외, 파일/줄 수 제한."""
import fnmatch
import re
from dataclasses import dataclass, field

# (이름, 정규표현식, 치환문) — 구체적인 패턴이 먼저 온다.
MASK_RULES = [
    ("private_key",
     r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
     "[MASKED_PRIVATE_KEY]"),
    ("anthropic_key", r"sk-ant-[A-Za-z0-9_\-]{16,}", "[MASKED_API_KEY]"),
    ("openai_style_key", r"\bsk-[A-Za-z0-9_\-]{20,}", "[MASKED_API_KEY]"),
    ("google_key", r"\bAIza[0-9A-Za-z_\-]{35}", "[MASKED_API_KEY]"),
    ("aws_access_key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "[MASKED_AWS_KEY]"),
    ("github_token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})", "[MASKED_TOKEN]"),
    ("slack_token", r"\bxox[baprs]-[A-Za-z0-9\-]{10,}", "[MASKED_TOKEN]"),
    ("jwt", r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}", "[MASKED_JWT]"),
    ("bearer", r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=\-]{20,}", r"\1[MASKED_TOKEN]"),
    # password = "값" 처럼 따옴표로 감싼 비밀 값 (변수 이름은 남긴다)
    ("quoted_secret",
     r"(?i)\b([\w.\-]*(?:password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key)[\w\-]*"
     r"[\"']?\s*[:=]\s*)([\"'])[^\"'\s]{6,}\2",
     r"\1\2[MASKED_SECRET]\2"),
    # .env 형식: API_KEY=값
    ("env_secret",
     r"(?m)^([+\-]?\s*(?:export\s+)?[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*=)(?!\[MASKED)\S{6,}$",
     r"\1[MASKED_SECRET]"),
    ("email", r"\b[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+\b", "[MASKED_EMAIL]"),
    ("kr_resident_no", r"\b\d{6}-[1-4]\d{6}\b", "[MASKED_ID_NUMBER]"),
    ("kr_phone", r"\b01[016789]-?\d{3,4}-?\d{4}\b", "[MASKED_PHONE]"),
]

# 내용 자체를 보내지 않는 파일 (이름만 알려 준다)
SENSITIVE_FILES = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "*.jks",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials", "credentials.json",
    "secrets.*", "*.secret", ".npmrc", ".pypirc", ".netrc",
]
SENSITIVE_ALLOW = [".env.example", ".env.sample", ".env.template"]


@dataclass
class Prepared:
    text: str = ""
    total_files: int = 0
    sent_files: int = 0
    total_lines: int = 0
    sent_lines: int = 0
    masked: dict = field(default_factory=dict)      # 규칙 이름 -> 마스킹 횟수
    excluded: list = field(default_factory=list)    # 민감 파일이라 내용 제외
    omitted: list = field(default_factory=list)     # 파일 수 제한으로 제외

    @property
    def masked_total(self):
        return sum(self.masked.values())


def split_diff(diff):
    """diff 텍스트를 파일 단위 [(경로, 블록 텍스트)]로 나눈다."""
    blocks = []
    current_path, current = None, []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if current:
                blocks.append((current_path, "\n".join(current)))
            match = re.match(r"diff --git a/.+? b/(.+)$", line)
            current_path, current = (match.group(1) if match else "?"), [line]
        else:
            current.append(line)
    if current:
        blocks.append((current_path, "\n".join(current)))
    return [(p, b) for p, b in blocks if p is not None or b.strip()]


def is_sensitive_file(path, extra_globs=()):
    name = (path or "").replace("\\", "/").rsplit("/", 1)[-1]
    if any(fnmatch.fnmatch(name, g) for g in SENSITIVE_ALLOW):
        return False
    globs = list(SENSITIVE_FILES) + list(extra_globs)
    return any(fnmatch.fnmatch(name, g) or fnmatch.fnmatch(path or "", g) for g in globs)


def mask_text(text, extra_patterns=()):
    """민감정보 패턴을 치환하고 (결과, {규칙: 횟수})를 돌려준다."""
    counts = {}
    rules = list(MASK_RULES) + [
        (f"custom_{i + 1}", pattern, "[MASKED_CUSTOM]") for i, pattern in enumerate(extra_patterns)
    ]
    for name, pattern, replacement in rules:
        text, n = re.subn(pattern, replacement, text)
        if n:
            counts[name] = n
    return text, counts


def _allocate(lengths, budget):
    """줄 수 예산을 파일들에 고르게 나눈다. 짧은 파일이 남긴 몫은 뒤에서 다시 나눈다."""
    if not lengths:
        return []
    share = max(1, budget // len(lengths))
    alloc = [min(n, share) for n in lengths]
    left = budget - sum(alloc)
    for i, n in enumerate(lengths):
        if left <= 0:
            break
        extra = min(n - alloc[i], left)
        alloc[i] += extra
        left -= extra
    return alloc


def prepare_diff(diff, safe=False, max_files=None, max_lines=None,
                 extra_patterns=(), extra_excludes=()):
    """AI에 보낼 diff를 만든다.

    safe=True: 민감 파일 내용 제외 + 패턴 마스킹 + 파일/줄 수 제한.
    safe=False: 줄 수 상한(max_lines)만 적용한다.
    """
    blocks = split_diff(diff)
    out = Prepared(total_files=len(blocks), total_lines=len(diff.splitlines()))

    if max_files is not None and len(blocks) > max_files:
        out.omitted = [p for p, _ in blocks[max_files:]]
        blocks = blocks[:max_files]

    bodies = []
    for path, block in blocks:
        if safe and is_sensitive_file(path, extra_excludes):
            out.excluded.append(path)
            block = f"diff --git a/{path} b/{path}\n[safe-mode] 민감 파일로 분류되어 내용을 전송하지 않습니다."
        bodies.append(block.splitlines())

    if max_lines is not None:
        alloc = _allocate([len(b) for b in bodies], max_lines)
    else:
        alloc = [len(b) for b in bodies]

    parts = []
    for lines, keep in zip(bodies, alloc):
        kept = lines[:keep]
        out.sent_lines += len(kept)
        if len(lines) > keep:
            kept.append(f"... (이 파일의 나머지 {len(lines) - keep}줄 생략)")
        parts.append("\n".join(kept))
    out.sent_files = len(parts)
    if out.omitted:
        parts.append("[전송 제한으로 내용이 생략된 파일]\n" + "\n".join(f"- {p}" for p in out.omitted))

    text = "\n".join(parts)
    if safe:
        text, out.masked = mask_text(text, extra_patterns)
    out.text = text
    return out
