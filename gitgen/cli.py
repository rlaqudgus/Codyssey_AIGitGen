"""CLI: 수집 -> (safe-mode) 가공 -> 프롬프트 -> AI API 1회 -> 검증/후처리 -> 출력."""
import argparse
import os
import sys

from .ai_client import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, DEFAULT_TEMPERATURE, AIError, generate
from .git_collect import GitError, collect
from .prompts import build_system, build_user
from .safe_mode import SAFE_MAX_FILES, SAFE_MAX_LINES, prepare_diff
from .validate import OutputError, extract_json, normalize_commit, normalize_pr, render_commit, render_pr_body


def log(level, message):
    print(f"[{level}] {message}")


def section(title, body):
    header = f"--- {title} ---"
    print(header)
    print(body)
    print("-" * len(header))
    print()


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--model", default=DEFAULT_MODEL, help=f"모델 이름 (기본: {DEFAULT_MODEL})")
    common.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE,
                        help=f"출력의 무작위성 0.0~2.0 (기본: {DEFAULT_TEMPERATURE}. 낮을수록 일관된 출력)")
    common.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                        help=f"응답 최대 토큰 수 (기본: {DEFAULT_MAX_TOKENS})")
    common.add_argument("--safe-mode", action="store_true",
                        help=f"민감정보 마스킹 + 민감 파일 제외 + 최대 {SAFE_MAX_FILES}개 파일/{SAFE_MAX_LINES}줄만 전송")
    common.add_argument("--reason", "-r", help="변경 이유/배경 (프롬프트에 포함)")

    parser = argparse.ArgumentParser(
        prog="main.py", description="git status/diff를 AI에 넘겨 커밋 메시지와 PR 초안을 생성합니다."
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="{commit,pr}")
    sub.add_parser("commit", parents=[common], help="커밋 메시지 생성")
    pr = sub.add_parser("pr", parents=[common], help="PR 제목/본문 초안 생성")
    pr.add_argument("--base", default="main", help="비교 기준 브랜치 (기본: main)")
    return parser


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    kind = args.command
    target = "커밋 메시지" if kind == "commit" else "PR 초안"

    # 1. Git 변경 사항 수집
    try:
        changes = collect(kind, getattr(args, "base", "main"))
    except GitError as e:
        log("ERROR", f"Git 변경 사항을 수집하지 못했습니다: {e}")
        return 1
    if not changes.has_changes:
        particle = "를" if kind == "commit" else "을"
        log("INFO", f"변경 사항이 없습니다. {target}{particle} 생성하지 않고 종료합니다.")
        return 0
    log("INFO", f"현재 브랜치: {changes.branch or '(알 수 없음)'}")
    log("INFO", f"Git status 수집 완료: {len(changes.files)}개 파일 변경 감지")
    log("INFO", f"Git diff 수집 완료: {changes.diff_lines}줄 — {changes.scope}")
    if changes.untracked:
        log("WARN", f"새 파일(untracked) {len(changes.untracked)}개는 diff에 내용이 없습니다. "
                    "`git add` 후 실행하면 내용까지 반영됩니다.")

    # 2. 전송할 diff 가공
    prepared = prepare_diff(changes.diff, safe=args.safe_mode)
    if args.safe_mode:
        log("INFO", f"safe-mode ON: 마스킹 {prepared.masked}건, 민감 파일 제외 {len(prepared.excluded)}개")
    if prepared.sent_lines < prepared.total_lines or prepared.omitted:
        log("WARN", f"diff 일부만 전송합니다: {prepared.sent_lines}/{prepared.total_lines}줄, "
                    f"{prepared.sent_files}/{prepared.total_files}개 파일")

    # 3. AI API 호출 (1회)
    key = os.environ.get("AI_API_KEY", "").strip()
    if not key:
        log("ERROR", "AI_API_KEY 환경변수가 설정되지 않았습니다.")
        print('  예) export AI_API_KEY="YOUR_KEY"          (macOS/Linux/Git Bash)')
        print('  예) $env:AI_API_KEY = "YOUR_KEY"          (Windows PowerShell)')
        return 1
    log("INFO", f"AI API 요청 중... (gemini / {args.model})")
    try:
        result = generate(args.model, key, build_system(kind),
                          build_user(kind, changes, prepared, args.reason),
                          temperature=args.temperature, max_tokens=args.max_tokens)
    except AIError as e:
        log("ERROR", f"AI API 호출 실패: {e}")
        return 1
    if result.truncated:
        log("WARN", "응답이 max_tokens에 걸려 잘렸습니다. --max-tokens 값을 늘려 보세요.")

    # 4. 검증 후처리
    try:
        data = extract_json(result.text)
        if kind == "commit":
            draft, notes = normalize_commit(data)
            text = render_commit(draft)
        else:
            draft, notes = normalize_pr(data)
            body = render_pr_body(draft)
    except OutputError as e:
        log("ERROR", f"모델 출력을 사용할 수 없습니다: {e}")
        return 1
    for note in notes:
        log("INFO", f"후처리: {note}")
    log("DONE", f"{target} 생성 완료")

    # 5. 출력
    print()
    if draft.summary:
        section("Summary", draft.summary)
    if kind == "commit":
        section("Commit Message", text)
    else:
        section("PR Title", draft.title)
        section("PR Body", body)
    log("INFO", f"AI API 호출 횟수: 1회 (입력 {result.input_tokens} / 출력 {result.output_tokens} 토큰)")
    log("INFO", "위 문구는 초안입니다. 내용을 검토·수정한 뒤 직접 적용하세요.")
    return 0
