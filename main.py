"""AI 기반 Git 커밋/PR 초안 생성기 진입점.

사용법:
    python main.py commit
    python main.py pr
"""
import sys

from gitgen.cli import main

if __name__ == "__main__":
    sys.exit(main())
