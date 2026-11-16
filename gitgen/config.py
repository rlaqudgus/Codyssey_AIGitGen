"""기본값과 팀 컨벤션 설정 파일(.ai-gitgen.json) 로딩."""
import copy
import json
import os

CONFIG_FILENAME = ".ai-gitgen.json"

# 길이 규칙 (미션 요구사항 5)
COMMIT_TITLE_RECOMMENDED = 50
COMMIT_TITLE_MAX = 72
PR_TITLE_MAX = 80

DEFAULTS = {
    "language": "ko",          # 생성 문구 언어: ko | en
    "base_branch": "main",     # pr 명령이 비교할 기준 브랜치
    "convention": {
        "name": "default",
        "commit_format": "<type>: <제목>",
        "commit_types": ["feat", "fix", "docs", "refactor", "test", "chore", "style", "perf"],
        "commit_rules": [],     # 팀 규칙 문장 (프롬프트에 그대로 추가)
        "commit_examples": [],  # 기존 커밋 제목 예시 (few-shot)
        "pr_title_format": "<type>: <제목>",
        "pr_rules": [],
        "pr_checklist": [],     # PR 본문 끝에 붙일 체크리스트 항목
    },
    "safe_mode": {
        "max_files": 10,        # safe-mode에서 전송할 최대 파일 수
        "max_lines": 200,       # safe-mode에서 전송할 최대 diff 줄 수
        "mask_patterns": [],    # 추가 마스킹 정규표현식
        "exclude_files": [],    # 내용을 보내지 않을 파일 glob (기본 목록에 추가)
    },
    # safe-mode가 아닐 때도 비용 폭주를 막는 상한
    "normal_max_lines": 1500,
}


class ConfigError(Exception):
    pass


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path=None, enabled=True):
    """설정을 읽어 (config, 읽은 파일 경로 또는 None)을 돌려준다.

    path를 직접 준 경우 파일이 없으면 오류, 주지 않은 경우 현재 폴더의
    .ai-gitgen.json이 있으면 읽고 없으면 기본값을 쓴다.
    """
    config = copy.deepcopy(DEFAULTS)
    if not enabled:
        return config, None
    target = path or CONFIG_FILENAME
    if not os.path.isfile(target):
        if path:
            raise ConfigError(f"컨벤션 파일을 찾을 수 없습니다: {path}")
        return config, None
    try:
        with open(target, encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"컨벤션 파일을 읽을 수 없습니다 ({target}): {e}")
    if not isinstance(loaded, dict):
        raise ConfigError(f"컨벤션 파일의 최상위는 JSON 객체여야 합니다: {target}")
    return _merge(config, loaded), target
