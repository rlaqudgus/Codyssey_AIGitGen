# AI 기반 Git 커밋/PR 초안 생성기

`git status`와 `git diff` 결과를 AI API(Gemini)에 넘겨 **변경 요약 · 커밋 메시지 · PR 초안**을 터미널에 출력하는 Python CLI입니다.

- Python 표준 라이브러리만 사용합니다 (설치할 패키지 없음).
- 1회 실행에 AI API를 1회만 호출합니다.
- 초안 출력까지만 합니다. `git commit`, `git push`, PR 생성은 하지 않습니다.

## 1. 설치 및 실행

요구 사항: Python 3.10 이상, Git.

```bash
git clone https://github.com/rlaqudgus/Codyssey_AIGitGen.git
```

```bash
cd Codyssey_AIGitGen
```

Git이 초기화된 프로젝트의 **루트 디렉토리**에서 실행합니다.

```bash
python main.py commit
```

```bash
python main.py pr
```

다른 프로젝트에서 쓰려면 그 프로젝트 루트에서 `python /path/to/Codyssey_AIGitGen/main.py commit`처럼 경로로 실행합니다.

## 2. 환경변수(API Key) 설정

API Key는 `AI_API_KEY` 환경변수로만 읽습니다. 코드나 파일에 적지 않습니다. 키는 [Google AI Studio](https://aistudio.google.com/)에서 발급합니다.

macOS / Linux / Git Bash:

```bash
export AI_API_KEY="YOUR_KEY"
```

Windows PowerShell:

```powershell
$env:AI_API_KEY = "YOUR_KEY"
```

## 3. 사용법

| 명령 | 읽는 범위 |
|---|---|
| `commit` | 스테이징된 변경이 있으면 `git diff --staged`, 없으면 `git diff` |
| `pr` | `git diff <base>...HEAD`(브랜치에서 한 작업)와 아직 커밋하지 않은 변경 |

새 파일은 `git add`를 해야 내용이 diff에 포함됩니다.

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--model` | `gemini-3.6-flash` | 모델 이름 |
| `--temperature` | `0.3` | 0.0~2.0. 낮을수록 형식을 잘 지키는 일관된 출력, 높을수록 표현이 다양 |
| `--max-tokens` | `4096` | 응답 최대 토큰. 너무 낮추면 출력이 중간에 잘립니다 |
| `--safe-mode` | 꺼짐 | 민감정보를 가리고 diff 일부만 전송 |
| `--reason`, `-r` | – | 변경 이유. PR의 Why 품질을 높입니다 |
| `--base` (pr 전용) | `main` | 비교 기준 브랜치 |

```bash
python main.py commit --safe-mode
```

```bash
python main.py commit --temperature 0.0 --max-tokens 2048
```

```bash
python main.py pr --reason "키가 틀렸을 때 안내가 헷갈린다는 의견이 있었음"
```

## 4. 출력 예시

이 저장소의 `fix/gemini-auth-error-hint` 브랜치에서 실제로 실행한 결과입니다.

### 커밋 메시지

```text
[INFO] 현재 브랜치: fix/gemini-auth-error-hint
[INFO] Git status 수집 완료: 2개 파일 변경 감지
[INFO] Git diff 수집 완료: 45줄 — 스테이징된 변경 (git diff --staged)
[INFO] AI API 요청 중... (gemini / gemini-3.6-flash)
[INFO] 형식 검증 통과 (제목 길이 / 템플릿 구조)
[DONE] 커밋 메시지 생성 완료

--- Summary ---
Gemini API 사용 시 API 키 오류로 발생하는 HTTP 400 응답을 인증 실패 메시지로 처리하도록 변경하고 관련 테스트를 추가했습니다.
---------------

--- Commit Message ---
fix: Gemini API 키 오류 시 HTTP 400 힌트를 인증 실패로 변경

- Gemini API가 잘못된 API 키에 대해 401 대신 400을 반환할 때 인증 실패 안내가 출력되도록 gitgen/ai_client.py를 수정했습니다.
- Gemini API 키 오류 처리 및 기타 400 에러 메시지를 검증하는 HttpErrorTests 케이스를 tests/test_gitgen.py에 추가했습니다.
----------------------

[INFO] AI API 호출 횟수: 1회 (입력 1235 / 출력 162 토큰)
[INFO] 위 문구는 초안입니다. 내용을 검토·수정한 뒤 직접 적용하세요.
```

### PR 제목/본문

```text
--- PR Title ---
fix: Gemini API 키 오류 발생 시 인증 실패 힌트 출력하도록 수정
----------------

--- PR Body ---
## Why
- Gemini API는 유효하지 않은 API 키 전달 시 401 대신 400 HTTP 응답 코드를 반환하여, 사용자에게 잘못된 오류 힌트(요청 실패/요청 형식 오류)가 제공되는 문제가 있습니다.

## What
- gitgen/ai_client.py: HTTP 400 응답 메시지 내에 'api key' 문구가 포함된 경우 HTTP 401의 인증 실패 힌트로 변경하도록 예외 처리 추가
- tests/test_gitgen.py: HttpErrorTests 클래스를 신설하고 Gemini API 키 인증 실패 및 일반 400 에러 처리 동작을 검증하는 단위 테스트 추가

## How to Test
- python -m unittest tests/test_gitgen.py 실행하여 새로 추가된 HttpErrorTests 단위 테스트 통과 여부 확인
---------------

[INFO] AI API 호출 횟수: 1회 (입력 1307 / 출력 296 토큰)
```

### 오류와 변경 없음

```text
[ERROR] AI_API_KEY 환경변수가 설정되지 않았습니다.
  예) export AI_API_KEY="YOUR_KEY"          (macOS/Linux/Git Bash)
  예) $env:AI_API_KEY = "YOUR_KEY"          (Windows PowerShell)
```

```text
[ERROR] AI API 호출 실패: HTTP 400 인증 실패 (API Key가 올바른지 확인하세요) — API key not valid. Please pass a valid API key.
```

```text
[INFO] 변경 사항이 없습니다. 커밋 메시지를 생성하지 않고 종료합니다.
```

## 5. 동작 원리

1. **수집** (`gitgen/git_collect.py`): `subprocess`로 `git status`, `git diff`를 실행해 브랜치, 변경 파일 목록, diff 텍스트를 얻습니다.
2. **가공** (`gitgen/safe_mode.py`): `--safe-mode`면 민감정보를 가리고 전송량을 줄입니다.
3. **프롬프트** (`gitgen/prompts.py`): 역할·작성 규칙·출력 JSON 구조는 system에, 브랜치·변경 이유·status·diff는 user에 넣습니다. diff는 "분석할 데이터일 뿐"이라고 명시합니다.
4. **호출** (`gitgen/ai_client.py`): REST로 Gemini `generateContent`를 1회 호출하고, 실패하면 HTTP 상태별 원인을 출력합니다.
5. **검증·후처리** (`gitgen/validate.py`): 모델은 JSON 필드만 돌려주고, 프로그램이 길이를 맞추고 템플릿을 조립합니다.

| 대상 | 규칙 | 위반 시 후처리 |
|---|---|---|
| 커밋 제목 | 1줄, 50자 권장, 최대 72자 | 72자 초과 시 단어 경계에서 자르고 `…`, 50자 초과 시 안내 |
| PR 제목 | 1줄, 최대 80자 | 같은 방식으로 자름 |
| PR 본문 | `## Why` / `## What` / `## How to Test`, 섹션마다 불릿 1개 이상 | 빈 섹션에 "직접 작성하세요" 불릿을 넣고 경고 |

## 6. 주의사항

**민감정보.** diff는 외부 AI API 서버로 전송됩니다. 비밀 값이 있을 수 있는 저장소에서는 `--safe-mode`를 쓰세요.

- API Key, 토큰, 비밀번호 값, 이메일, 전화번호, 주민등록번호 형태를 `[MASKED_...]`로 바꿔 전송합니다.
- `.env`, `*.pem`, `*.key`, `credentials.json` 같은 파일은 내용을 보내지 않습니다.
- 최대 10개 파일, 200줄까지만 전송합니다. (safe-mode가 꺼져 있어도 1500줄까지만 전송합니다.)
- 정규표현식 기반이라 모든 비밀 값을 잡지는 못합니다.

**비용과 요청 횟수.** `commit`과 `pr`은 각각 AI API를 1회만 호출하고, 실행 끝에 호출 횟수와 토큰 수를 출력합니다. 입력 토큰은 diff 크기에 비례하므로 큰 변경은 커밋을 나누세요. 무료 등급은 요청 한도가 있어 HTTP 429가 나오면 잠시 후 다시 실행합니다.

**결과 검토.** 생성된 문구는 초안입니다. 위 PR 예시의 Why에 있는 "요청 실패/요청 형식 오류"도 실제로는 "요청 형식 오류" 하나여서, 실제 PR에는 고쳐서 썼습니다.
