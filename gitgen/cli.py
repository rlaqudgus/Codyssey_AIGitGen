"""CLI: 수집 -> (safe-mode) 가공 -> 프롬프트 -> AI API 1회 -> 검증/후처리 -> 출력."""
import argparse
import re
import sys

from . import __version__
from .ai_client import (
    DEFAULT_MAX_TOKENS, DEFAULT_MODELS, DEFAULT_TEMPERATURE, AIError, generate, resolve_provider,
)
from .config import ConfigError, load_config
from .git_collect import GitError, collect
from .prompts import build_system, build_user
from .safe_mode import prepare_diff
from .validate import (
    OutputError, check_commit, check_pr, extract_json, normalize_commit, normalize_pr,
    render_commit, render_pr_body, verify_commit_text, verify_pr_text,
)

MAX_CALLS = 2   # 1회 실행에서 허용하는 AI API 호출 상한 (--retry 사용 시에만 2회)


def log(level, message):
    print(f"[{level}] {message}")


def section(title, body):
    header = f"--- {title} ---"
    print(header)
    print(body)
    print("-" * len(header))
    print()


def _temperature(value):
    number = float(value)
    if not 0.0 <= number <= 2.0:
        raise argparse.ArgumentTypeError("0.0 ~ 2.0 사이 값이어야 합니다")
    return number


def _positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("1 이상의 정수여야 합니다")
    return number


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    ai = common.add_argument_group("AI API 옵션")
    ai.add_argument("--provider", choices=["gemini", "claude"],
                    help="AI 제공자 (기본: AI_PROVIDER 환경변수 또는 설정된 API Key로 자동 선택)")
    ai.add_argument("--model", help=f"모델 이름 (기본: gemini={DEFAULT_MODELS['gemini']}, claude={DEFAULT_MODELS['claude']})")
    ai.add_argument("--temperature", type=_temperature, default=None,
                    help=f"출력의 무작위성 0.0~2.0 (기본: {DEFAULT_TEMPERATURE}. 낮을수록 일관된 출력)")
    ai.add_argument("--max-tokens", type=_positive, default=DEFAULT_MAX_TOKENS,
                    help=f"응답 최대 토큰 수 (기본: {DEFAULT_MAX_TOKENS})")
    ai.add_argument("--timeout", type=_positive, default=90, help="API 응답 대기 시간(초) (기본: 90)")
    ai.add_argument("--retry", action="store_true",
                    help="형식 규칙을 어긴 출력이면 1회 재생성 요청 (총 호출 최대 2회). 기본은 후처리만")
    ai.add_argument("--dry-run", action="store_true",
                    help="AI API를 호출하지 않고 전송될 프롬프트만 출력 (비용 0)")

    safe = common.add_argument_group("안전 모드")
    safe.add_argument("--safe-mode", action="store_true",
                      help="민감정보 마스킹 + 민감 파일 내용 제외 + diff 전송량 제한")
    safe.add_argument("--max-files", type=_positive, help="전송할 최대 파일 수 (safe-mode 기본: 10)")
    safe.add_argument("--max-lines", type=_positive, help="전송할 최대 diff 줄 수 (safe-mode 기본: 200, 일반: 1500)")
    safe.add_argument("--mask-pattern", action="append", default=[], metavar="REGEX",
                      help="safe-mode에서 추가로 마스킹할 정규표현식 (여러 번 지정 가능)")

    ctx = common.add_argument_group("컨텍스트")
    ctx.add_argument("--reason", "-r", help="변경 이유/배경 (프롬프트에 포함되어 Why 품질을 높임)")
    ctx.add_argument("--convention", metavar="FILE", help="팀 컨벤션 JSON 파일 (기본: ./.ai-gitgen.json 이 있으면 사용)")
    ctx.add_argument("--no-convention", action="store_true", help="설정 파일을 무시하고 기본 컨벤션 사용")
    ctx.add_argument("--lang", choices=["ko", "en"], help="생성 문구 언어 (기본: ko)")

    parser = argparse.ArgumentParser(
        prog="main.py", description="git status/diff를 AI에 넘겨 커밋 메시지와 PR 초안을 생성합니다."
    )
    parser.add_argument("--version", action="version", version=f"ai-gitgen {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="{commit,pr}")
    sub.add_parser("commit", parents=[common], help="커밋 메시지 생성")
    pr = sub.add_parser("pr", parents=[common], help="PR 제목/본문 초안 생성")
    pr.add_argument("--base", help="비교 기준 브랜치 (기본: main)")
    return parser


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    kind = args.command

    # 0. 설정
    try:
        config, config_path = load_config(args.convention, enabled=not args.no_convention)
    except ConfigError as e:
        log("ERROR", str(e))
        return 1
    if args.lang:
        config["language"] = args.lang
    extra_patterns = list(config["safe_mode"]["mask_patterns"]) + args.mask_pattern
    for pattern in extra_patterns:
        try:
            re.compile(pattern)
        except re.error as e:
            log("ERROR", f"마스킹 정규표현식이 올바르지 않습니다 ({pattern}): {e}")
            return 1

    # 1. Git 변경 사항 수집
    base = getattr(args, "base", None) or config["base_branch"]
    try:
        changes = collect(kind, base)
    except GitError as e:
        log("ERROR", f"Git 변경 사항을 수집하지 못했습니다: {e}")
        return 1
    target = "커밋 메시지" if kind == "commit" else "PR 초안"
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
    if config_path:
        log("INFO", f"컨벤션 적용: {config['convention'].get('name', '?')} ({config_path})")

    # 2. 전송할 diff 가공
    safe_cfg = config["safe_mode"]
    max_files = args.max_files or (safe_cfg["max_files"] if args.safe_mode else None)
    max_lines = args.max_lines or (safe_cfg["max_lines"] if args.safe_mode else config["normal_max_lines"])
    prepared = prepare_diff(
        changes.diff, safe=args.safe_mode, max_files=max_files, max_lines=max_lines,
        extra_patterns=extra_patterns, extra_excludes=safe_cfg["exclude_files"],
    )
    if args.safe_mode:
        log("INFO", f"safe-mode ON: 최대 {max_files}개 파일 / {max_lines}줄 전송, "
                    f"마스킹 {prepared.masked_total}건, 민감 파일 제외 {len(prepared.excluded)}개")
        for name, count in prepared.masked.items():
            log("INFO", f"  마스킹 {name}: {count}건")
    if prepared.sent_lines < prepared.total_lines or prepared.omitted:
        log("WARN", f"diff 일부만 전송합니다: {prepared.sent_lines}/{prepared.total_lines}줄, "
                    f"{prepared.sent_files}/{prepared.total_files}개 파일 (--max-lines / --max-files 로 조정)")

    # 3. 프롬프트
    system = build_system(kind, config)
    user = build_user(kind, changes, prepared, reason=args.reason)
    if args.dry_run:
        section("System Prompt", system)
        section("User Prompt", user)
        log("INFO", "dry-run: AI API를 호출하지 않았습니다. AI API 호출 횟수: 0회")
        return 0

    # 4. AI API 호출
    try:
        provider, key = resolve_provider(args.provider)
    except AIError as e:
        log("ERROR", str(e))
        return 1
    if not key:
        log("ERROR", "AI_API_KEY 환경변수가 설정되지 않았습니다.")
        print('  예) export AI_API_KEY="YOUR_KEY"          (macOS/Linux/Git Bash)')
        print('  예) $env:AI_API_KEY = "YOUR_KEY"          (Windows PowerShell)')
        return 1
    model = args.model or DEFAULT_MODELS[provider]
    checker = check_commit if kind == "commit" else check_pr
    calls = tokens_in = tokens_out = 0
    data = None
    feedback = None
    try:
        while calls < (MAX_CALLS if args.retry else 1):
            log("INFO", f"AI API 요청 중... ({provider} / {model})" + (" — 재생성" if calls else ""))
            prompt = user if not feedback else build_user(kind, changes, prepared, args.reason, feedback)
            calls += 1
            result = generate(provider, model, key, system, prompt,
                              temperature=args.temperature, max_tokens=args.max_tokens, timeout=args.timeout)
            tokens_in += result.input_tokens
            tokens_out += result.output_tokens
            if result.truncated:
                log("WARN", "응답이 max_tokens에 걸려 잘렸습니다. --max-tokens 값을 늘려 보세요.")
            try:
                data = extract_json(result.text)
                feedback = checker(data)
            except OutputError as e:
                data, feedback = None, [str(e)]
            if not feedback:
                break
            for issue in feedback:
                log("WARN", f"형식 규칙 위반: {issue}")
    except AIError as e:
        log("ERROR", f"AI API 호출 실패: {e}")
        log("INFO", f"AI API 호출 횟수: {calls}회")
        return 1

    # 5. 검증 후처리 + 출력
    try:
        if data is None:
            raise OutputError(feedback[0])
        if kind == "commit":
            draft, notes = normalize_commit(data)
            text = render_commit(draft)
            problems = verify_commit_text(text)
        else:
            draft, notes = normalize_pr(data, config["convention"].get("pr_checklist", []))
            body = render_pr_body(draft)
            problems = verify_pr_text(draft.title, body)
    except OutputError as e:
        log("ERROR", f"모델 출력을 사용할 수 없습니다: {e} (--retry 또는 --max-tokens 조정을 시도하세요)")
        log("INFO", f"AI API 호출 횟수: {calls}회")
        return 1
    for note in notes:
        log("INFO", f"후처리: {note}")
    if problems:
        for problem in problems:
            log("WARN", f"형식 검증 실패: {problem}")
    else:
        log("INFO", "형식 검증 통과 (제목 길이 / 템플릿 구조)")
    log("DONE", f"{target} 생성 완료")
    print()
    if draft.summary:
        section("Summary", draft.summary)
    if kind == "commit":
        section("Commit Message", text)
    else:
        section("PR Title", draft.title)
        section("PR Body", body)
    log("INFO", f"AI API 호출 횟수: {calls}회 (입력 {tokens_in} / 출력 {tokens_out} 토큰)")
    log("INFO", "위 문구는 초안입니다. 내용을 검토·수정한 뒤 직접 적용하세요.")
    return 0
