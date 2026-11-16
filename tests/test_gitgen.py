"""테스트. AI API는 호출하지 않는다 (generate를 가짜로 바꿔 끼운다).

실행: python -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitgen import ai_client, cli, validate  # noqa: E402
from gitgen.ai_client import AIError, AIResult  # noqa: E402
from gitgen.git_collect import parse_status  # noqa: E402
from gitgen.safe_mode import is_sensitive_file, mask_text, prepare_diff  # noqa: E402

SAMPLE_DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,4 @@
 import os
+API_KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
+ADMIN = "admin@example.com"
 print("hi")
diff --git a/.env b/.env
--- a/.env
+++ b/.env
@@ -1 +1 @@
-DB_PASSWORD=oldpassword1
+DB_PASSWORD=newpassword2
"""


class GitTests(unittest.TestCase):
    def test_parse_status(self):
        branch, files = parse_status("## feat/x...origin/feat/x [ahead 1]\n M a.py\n?? new.txt\nR  old.py -> new.py\n")
        self.assertEqual(branch, "feat/x")
        self.assertEqual(files, [(" M", "a.py"), ("??", "new.txt"), ("R ", "new.py")])


class SafeModeTests(unittest.TestCase):
    def test_masking(self):
        text, count = mask_text('token = "abcd1234efgh"\nmail: kim@test.co.kr\n+SECRET_KEY=supersecretvalue\n010-1234-5678')
        for leaked in ("abcd1234efgh", "kim@test.co.kr", "supersecretvalue", "010-1234-5678"):
            self.assertNotIn(leaked, text)
        self.assertEqual(count, 4)

    def test_code_is_not_masked(self):
        line = 'token = os.environ.get("AI_API_KEY")'
        self.assertEqual(mask_text(line)[0], line)

    def test_sensitive_files(self):
        self.assertTrue(is_sensitive_file(".env"))
        self.assertTrue(is_sensitive_file("config/server.pem"))
        self.assertFalse(is_sensitive_file(".env.example"))
        self.assertFalse(is_sensitive_file("app.py"))

    def test_safe_on_vs_off(self):
        off = prepare_diff(SAMPLE_DIFF, safe=False)
        on = prepare_diff(SAMPLE_DIFF, safe=True)
        self.assertIn("sk-ant-api03", off.text)
        for leaked in ("sk-ant-api03", "admin@example.com", "newpassword2"):
            self.assertNotIn(leaked, on.text)
        self.assertEqual(on.excluded, [".env"])
        self.assertEqual(on.masked, 2)

    def test_limits(self):
        big = "".join(f"diff --git a/f{i}.py b/f{i}.py\n" + "+line\n" * 40 for i in range(12))
        out = prepare_diff(big, safe=True)
        self.assertEqual((out.sent_files, len(out.omitted)), (10, 2))
        self.assertLessEqual(out.sent_lines, 200)
        self.assertIn("생략", out.text)


class ValidateTests(unittest.TestCase):
    def test_extract_json(self):
        self.assertEqual(validate.extract_json('```json\n{"title": "a"}\n```'), {"title": "a"})
        with self.assertRaises(validate.OutputError):
            validate.extract_json("JSON 없음")

    def test_commit_title_is_cleaned_and_truncated(self):
        draft, notes = validate.normalize_commit({"title": "feat: " + "아주 긴 제목 " * 20, "bullets": ["- a.py 수정"]})
        self.assertLessEqual(len(draft.title), 72)
        self.assertTrue(notes)
        self.assertEqual(draft.bullets, ["a.py 수정"])
        self.assertEqual(validate.verify_commit_text(validate.render_commit(draft)), [])
        draft, _ = validate.normalize_commit({"title": "`fix: 버그 수정.`\n둘째 줄"})
        self.assertEqual(validate.render_commit(draft), "fix: 버그 수정")

    def test_missing_title_raises(self):
        with self.assertRaises(validate.OutputError):
            validate.normalize_commit({"title": "", "bullets": ["x"]})

    def test_pr_sections_are_guaranteed(self):
        draft, notes = validate.normalize_pr({"title": "t" * 200, "why": ["이유"], "what": "- 하나\n- 둘", "how_to_test": []})
        body = validate.render_pr_body(draft)
        self.assertLessEqual(len(draft.title), 80)
        self.assertEqual(validate.verify_pr_text(draft.title, body), [])
        self.assertEqual(draft.sections["what"], ["하나", "둘"])
        self.assertEqual(len(notes), 2)     # 제목 자름 + 빈 섹션 보정

    def test_verify_detects_missing_bullet(self):
        self.assertTrue(validate.verify_pr_text("t", "## Why\n- a\n\n## What\n\n## How to Test\n- c"))


class HttpErrorTests(unittest.TestCase):
    def call_with_error(self, status, message):
        body = io.BytesIO(json.dumps({"error": {"message": message}}).encode("utf-8"))
        error = urllib.error.HTTPError("https://example.invalid", status, "err", {}, body)
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(AIError) as caught:
                ai_client.generate("gemini-3.6-flash", "bad", "s", "u")
        return str(caught.exception)

    def test_invalid_key_is_reported_as_auth_failure(self):
        message = self.call_with_error(400, "API key not valid. Please pass a valid API key.")
        self.assertIn("HTTP 400 인증 실패", message)
        self.assertIn("API key not valid", message)

    def test_other_400_keeps_request_hint(self):
        self.assertIn("요청 형식 오류", self.call_with_error(400, "Invalid value at 'generation_config'"))


def git(*args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.old_cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        git("init", "-q", "-b", "main")
        with open("app.py", "w", encoding="utf-8") as f:
            f.write("print('hi')\n")
        git("add", ".")
        git("commit", "-q", "-m", "init")
        self.env = mock.patch.dict(os.environ, {"AI_API_KEY": "test-key"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        os.chdir(self.old_cwd)
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def run_cli(self, *argv, reply=None, error=None):
        out = io.StringIO()
        fake = mock.Mock(side_effect=error) if error else mock.Mock(
            return_value=AIResult(text=json.dumps(reply or {}, ensure_ascii=False), input_tokens=10, output_tokens=5))
        with mock.patch.object(cli, "generate", fake), contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue(), fake

    def modify(self):
        with open("app.py", "a", encoding="utf-8") as f:
            f.write('KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"\n')

    def test_no_changes(self):
        code, out, fake = self.run_cli("commit")
        self.assertEqual(code, 0)
        self.assertIn("변경 사항이 없습니다", out)
        fake.assert_not_called()

    def test_commit(self):
        self.modify()
        code, out, fake = self.run_cli("commit", reply={
            "summary": "키 상수 추가", "title": "feat: 키 상수 추가", "bullets": ["app.py에 KEY 상수 추가"]})
        self.assertEqual(code, 0)
        self.assertIn("--- Commit Message ---", out)
        self.assertIn("feat: 키 상수 추가\n\n- app.py에 KEY 상수 추가", out)
        self.assertIn("AI API 호출 횟수: 1회", out)
        self.assertEqual(fake.call_count, 1)

    def test_options_reach_the_api_call(self):
        self.modify()
        _, _, fake = self.run_cli("commit", "--model", "m1", "--temperature", "0.9", "--max-tokens", "123",
                                  reply={"title": "feat: x"})
        self.assertEqual(fake.call_args[0][0], "m1")
        self.assertEqual(fake.call_args[1], {"temperature": 0.9, "max_tokens": 123})

    def test_safe_mode_masks_prompt(self):
        self.modify()
        reply = {"title": "feat: x", "bullets": ["app.py"]}
        _, _, plain = self.run_cli("commit", reply=reply)
        _, out, safe = self.run_cli("commit", "--safe-mode", reply=reply)
        self.assertIn("sk-ant-api03", plain.call_args[0][3])
        self.assertNotIn("sk-ant-api03", safe.call_args[0][3])
        self.assertIn("safe-mode ON", out)

    def test_pr(self):
        git("checkout", "-q", "-b", "feature/x")
        self.modify()
        git("commit", "-q", "-am", "work")
        code, out, _ = self.run_cli("pr", reply={
            "summary": "s", "title": "feat: x", "why": ["w"], "what": ["app.py 변경"], "how_to_test": ["python app.py"]})
        self.assertEqual(code, 0)
        self.assertIn("현재 브랜치: feature/x", out)
        for marker in ("--- PR Title ---", "--- PR Body ---", "## Why", "## What", "## How to Test"):
            self.assertIn(marker, out)

    def test_missing_key(self):
        self.modify()
        os.environ.pop("AI_API_KEY")
        code, out, fake = self.run_cli("commit")
        self.assertEqual(code, 1)
        self.assertIn("AI_API_KEY 환경변수가 설정되지 않았습니다", out)
        fake.assert_not_called()

    def test_api_error(self):
        self.modify()
        code, out, _ = self.run_cli("commit", error=AIError("HTTP 401 인증 실패"))
        self.assertEqual(code, 1)
        self.assertIn("AI API 호출 실패: HTTP 401 인증 실패", out)

    def test_not_a_repo_root(self):
        os.mkdir("sub")
        os.chdir("sub")
        code, out, _ = self.run_cli("commit")
        self.assertEqual(code, 1)
        self.assertIn("프로젝트 루트", out)


if __name__ == "__main__":
    unittest.main()
