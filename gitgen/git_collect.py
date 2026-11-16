"""Git 변경 사항 수집. 사용하는 git 명령은 status 와 diff 두 가지뿐이다."""
import os
import re
import subprocess
from dataclasses import dataclass, field


class GitError(Exception):
    pass


@dataclass
class Changes:
    branch: str = ""
    files: list = field(default_factory=list)   # [(상태코드, 경로)]
    diff: str = ""
    scope: str = ""                             # diff가 무엇을 비교한 것인지 설명
    untracked: list = field(default_factory=list)

    @property
    def has_changes(self):
        return bool(self.files) or bool(self.diff.strip())

    @property
    def diff_lines(self):
        return len(self.diff.splitlines())


def run_git(args):
    """git 명령을 실행해 표준 출력을 돌려준다. 실패하면 GitError."""
    cmd = ["git", "-c", "core.quotepath=false", *args]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError:
        raise GitError("git 실행 파일을 찾을 수 없습니다. Git이 설치되어 있는지 확인하세요.")
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git {' '.join(args)} 실패")
    return result.stdout


def parse_status(text):
    """`git status --porcelain=v1 --branch` 출력을 (브랜치, 파일 목록)으로 바꾼다."""
    branch = ""
    files = []
    for line in text.splitlines():
        if line.startswith("## "):
            head = line[3:]
            if head.startswith("No commits yet on "):
                branch = head[len("No commits yet on "):]
            elif head.startswith("HEAD (no branch)"):
                branch = "HEAD"
            else:
                branch = head.split("...")[0].split(" ")[0]
        elif len(line) > 3:
            code, path = line[:2], line[3:]
            if " -> " in path:                  # 이름 변경: "old -> new"
                path = path.split(" -> ", 1)[1]
            files.append((code, path.strip('"')))
    return branch, files


def files_in_diff(diff):
    return re.findall(r"^diff --git a/.+? b/(.+)$", diff, flags=re.MULTILINE)


def collect(mode, base_branch="main"):
    """mode: "commit" 또는 "pr". 현재 폴더(프로젝트 루트)의 변경 사항을 모은다."""
    if not os.path.exists(".git"):
        raise GitError(
            "현재 폴더가 Git 프로젝트 루트가 아닙니다. "
            "`git init` 된 프로젝트의 루트 디렉토리에서 실행하세요."
        )
    branch, files = parse_status(run_git(["status", "--porcelain=v1", "--branch"]))
    changes = Changes(branch=branch, files=files)
    changes.untracked = [path for code, path in files if code == "??"]

    staged = run_git(["diff", "--staged"])
    unstaged = run_git(["diff"])

    if mode == "commit":
        # 스테이징한 것이 있으면 그것이 곧 "이번 커밋"이다. 없으면 작업 폴더 전체.
        if staged.strip():
            changes.diff, changes.scope = staged, "스테이징된 변경 (git diff --staged)"
            # 이번 커밋에 들어가지 않는 파일은 목록에서 뺀다.
            changes.files = [(c, p) for c, p in files if c[0] not in (" ", "?")]
            changes.untracked = []
        else:
            changes.diff, changes.scope = unstaged, "작업 폴더 변경 (git diff)"
        return changes

    # pr: 기준 브랜치에서 갈라진 이후의 커밋 + 아직 커밋하지 않은 변경
    branch_diff = ""
    if branch and branch not in (base_branch, "HEAD"):
        for ref in (base_branch, f"origin/{base_branch}"):
            try:
                branch_diff = run_git(["diff", f"{ref}...HEAD"])
                changes.scope = f"브랜치 변경 (git diff {ref}...HEAD)"
                break
            except GitError:
                continue
    parts = [p for p in (branch_diff, staged, unstaged) if p.strip()]
    changes.diff = "\n".join(parts)
    if not branch_diff.strip():
        changes.scope = "작업 폴더 변경 (git diff)"
    elif staged.strip() or unstaged.strip():
        changes.scope += " + 미커밋 변경"
    # 브랜치에 이미 커밋된 파일은 status에 안 나오므로 diff에서 보충한다.
    known = {path for _, path in changes.files}
    for path in files_in_diff(changes.diff):
        if path not in known:
            known.add(path)
            changes.files.append(("  ", path))
    return changes
