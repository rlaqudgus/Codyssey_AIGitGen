"""모델 출력 검증과 후처리.

모델은 JSON 필드만 돌려주고, 이 모듈이
1) JSON 추출  2) 규칙 위반 점검(check_*)  3) 후처리(normalize_*)  4) 템플릿 조립(render_*)
5) 최종 텍스트 재검증(verify_*)을 맡는다. 그래서 형식 규칙은 모델의 실수와 무관하게 보장된다.
"""
import json
import re
from dataclasses import dataclass, field

from .config import COMMIT_TITLE_MAX, COMMIT_TITLE_RECOMMENDED, PR_TITLE_MAX

PLACEHOLDER = "(AI가 내용을 생성하지 못했습니다. 직접 작성하세요)"
PR_SECTIONS = [("why", "Why"), ("what", "What"), ("how_to_test", "How to Test")]
MAX_BULLETS = 5


class OutputError(Exception):
    pass


@dataclass
class CommitDraft:
    summary: str
    title: str
    bullets: list = field(default_factory=list)


@dataclass
class PRDraft:
    summary: str
    title: str
    sections: dict = field(default_factory=dict)    # why / what / how_to_test -> [불릿]
    checklist: list = field(default_factory=list)


def extract_json(text):
    """모델 응답에서 JSON 객체 하나를 꺼낸다. 코드 펜스나 앞뒤 설명이 있어도 견딘다."""
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start < 0:
        raise OutputError("모델 응답에서 JSON 객체를 찾지 못했습니다.")
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as e:
        raise OutputError(f"모델 응답 JSON 해석 실패: {e.msg}")
    if not isinstance(data, dict):
        raise OutputError("모델 응답이 JSON 객체가 아닙니다.")
    return data


def _one_line(value):
    return " ".join(str(value or "").split())


def _clean_title(value):
    title = _one_line(str(value or "").strip().splitlines()[0] if str(value or "").strip() else "")
    title = title.strip("`\"' ")
    return title.rstrip(".。")


def _clean_bullets(value):
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        return []
    bullets = []
    for item in value:
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", _one_line(item))
        if line:
            bullets.append(line)
    return bullets


def fit_title(title, max_len):
    """제목이 max_len을 넘으면 단어 경계에서 잘라 말줄임표를 붙인다."""
    if len(title) <= max_len:
        return title
    cut = title[: max_len - 1]
    space = cut.rfind(" ")
    if space > max_len * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,:;-") + "…"


# ---------- 규칙 위반 점검 (재생성 요청용 피드백) ----------

def check_commit(data):
    issues = []
    title = _clean_title(data.get("title"))
    if not title:
        issues.append("title이 비어 있다. 커밋 제목 1줄은 필수다.")
    elif len(title) > COMMIT_TITLE_MAX:
        issues.append(f"title이 {len(title)}자다. {COMMIT_TITLE_MAX}자 이하(권장 {COMMIT_TITLE_RECOMMENDED}자)로 줄여라.")
    if not _clean_bullets(data.get("bullets")):
        issues.append("bullets가 비어 있다. 핵심 변경 사항을 1개 이상 써라.")
    return issues


def check_pr(data):
    issues = []
    title = _clean_title(data.get("title"))
    if not title:
        issues.append("title이 비어 있다. PR 제목 1줄은 필수다.")
    elif len(title) > PR_TITLE_MAX:
        issues.append(f"title이 {len(title)}자다. {PR_TITLE_MAX}자 이하로 줄여라.")
    for key, header in PR_SECTIONS:
        if not _clean_bullets(data.get(key)):
            issues.append(f"{key}({header}) 항목이 비어 있다. 1개 이상 써라.")
    return issues


# ---------- 후처리 ----------

def normalize_commit(data):
    """(CommitDraft, 후처리 내역)을 돌려준다. 제목이 아예 없으면 OutputError."""
    notes = []
    title = _clean_title(data.get("title"))
    if not title:
        raise OutputError("모델이 커밋 제목을 생성하지 못했습니다.")
    if len(title) > COMMIT_TITLE_MAX:
        notes.append(f"커밋 제목 {len(title)}자 → 최대 {COMMIT_TITLE_MAX}자로 잘랐습니다.")
        title = fit_title(title, COMMIT_TITLE_MAX)
    elif len(title) > COMMIT_TITLE_RECOMMENDED:
        notes.append(f"커밋 제목이 {len(title)}자입니다 (권장 {COMMIT_TITLE_RECOMMENDED}자 이내, 최대 {COMMIT_TITLE_MAX}자).")
    bullets = _clean_bullets(data.get("bullets"))
    if len(bullets) > MAX_BULLETS:
        notes.append(f"본문 불릿 {len(bullets)}개 → {MAX_BULLETS}개로 줄였습니다.")
        bullets = bullets[:MAX_BULLETS]
    return CommitDraft(summary=_one_line(data.get("summary")), title=title, bullets=bullets), notes


def normalize_pr(data, checklist=()):
    notes = []
    title = _clean_title(data.get("title"))
    if not title:
        raise OutputError("모델이 PR 제목을 생성하지 못했습니다.")
    if len(title) > PR_TITLE_MAX:
        notes.append(f"PR 제목 {len(title)}자 → 최대 {PR_TITLE_MAX}자로 잘랐습니다.")
        title = fit_title(title, PR_TITLE_MAX)
    sections = {}
    for key, header in PR_SECTIONS:
        bullets = _clean_bullets(data.get(key))
        if not bullets:
            notes.append(f"{header} 섹션이 비어 있어 안내 문구를 넣었습니다. 직접 채워 주세요.")
            bullets = [PLACEHOLDER]
        sections[key] = bullets[:MAX_BULLETS]
    draft = PRDraft(summary=_one_line(data.get("summary")), title=title,
                    sections=sections, checklist=list(checklist))
    return draft, notes


# ---------- 템플릿 조립 ----------

def render_commit(draft):
    if not draft.bullets:
        return draft.title
    return draft.title + "\n\n" + "\n".join(f"- {b}" for b in draft.bullets)


def render_pr_body(draft):
    blocks = []
    for key, header in PR_SECTIONS:
        blocks.append(f"## {header}\n" + "\n".join(f"- {b}" for b in draft.sections[key]))
    if draft.checklist:
        blocks.append("## Checklist\n" + "\n".join(f"- [ ] {item}" for item in draft.checklist))
    return "\n\n".join(blocks)


# ---------- 최종 텍스트 재검증 ----------

def verify_commit_text(text):
    problems = []
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        problems.append("커밋 제목이 없습니다.")
    elif len(lines[0]) > COMMIT_TITLE_MAX:
        problems.append(f"커밋 제목이 {COMMIT_TITLE_MAX}자를 넘습니다.")
    if len(lines) > 1 and lines[1].strip():
        problems.append("제목과 본문 사이에 빈 줄이 없습니다.")
    return problems


def verify_pr_text(title, body):
    problems = []
    if not title.strip() or "\n" in title:
        problems.append("PR 제목은 1줄이어야 합니다.")
    if len(title) > PR_TITLE_MAX:
        problems.append(f"PR 제목이 {PR_TITLE_MAX}자를 넘습니다.")
    for _, header in PR_SECTIONS:
        match = re.search(rf"^## {re.escape(header)}\n((?:(?!^## ).*\n?)*)", body, flags=re.MULTILINE)
        if not match:
            problems.append(f"'## {header}' 섹션 헤더가 없습니다.")
        elif not re.search(r"^- \S", match.group(1), flags=re.MULTILINE):
            problems.append(f"'## {header}' 섹션에 불릿이 없습니다.")
    return problems
