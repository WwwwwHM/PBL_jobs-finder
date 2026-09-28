"""Generated skill limits apply to tags and Markdown, including packed lists."""

import unittest
from unittest.mock import Mock

from pbl_jobs_finder.exceptions import ResumeResponseError
from pbl_jobs_finder.modules.resume_skills import (
    refine_markdown_skills,
    select_generated_skills,
    validate_generated_skills,
    validate_markdown_skills,
)
from pbl_jobs_finder.utils.llm_client import LLMServiceError


class ResumeSkillsTests(unittest.TestCase):
    def test_fenced_selection_json_is_accepted(self) -> None:
        client = Mock()
        client.complete.return_value = '```json\n{"selected_ids":[12,1,5]}\n```'
        selected, _ = select_generated_skills(
            [f"skill{i}" for i in range(13)], "工程师", client
        )
        self.assertEqual(selected, ["skill12", "skill1", "skill5"])

    def test_invalid_selection_uses_explicit_matches_without_inventing_skills(self) -> None:
        skills = [f"skill{i}" for i in range(13)] + ["Java", "JavaScript", "MySQL"]
        for response in (
            "not json", "[]", '{"selected_ids":[999]}', '{"selected_ids":[true]}',
            '{"selected_ids":[1,1]}', '{"selected_ids":["Java"]}',
            '{"selected_ids":[0,1,2,3,4,5,6,7,8,9,10]}',
        ):
            with self.subTest(response=response):
                client = Mock()
                client.complete.return_value = response
                selected, _ = select_generated_skills(skills, "Java 后端，要求 MySQL", client)
                self.assertEqual(selected, ["Java", "MySQL"])
                client.complete.assert_called_once()

    def test_timeout_with_unknown_role_omits_skills_and_preserves_other_text(self) -> None:
        client = Mock()
        client.complete.side_effect = LLMServiceError("timeout")
        prefix = "# 候选人\n\n## 经历\n- 完成系统开发。\n\n"
        suffix = "## 教育\n学校及时间保持原样\n"
        markdown = prefix + "## 专业技能\n" + "、".join(f"skill{i}" for i in range(13)) + "\n\n" + suffix
        result = refine_markdown_skills(markdown, "研究岗位", client)
        self.assertEqual(result, prefix + suffix)

    def test_multiple_markdown_sections_are_merged_without_changing_experience(self) -> None:
        client = Mock()
        client.complete.return_value = '{"selected_ids":[12,1]}'
        experience = "## 经历\n- 原始描述、数字 24、工具保持原样。\n\n"
        markdown = "# 候选人\n\n## **专业技能**\n" + "\n".join(
            f"- skill{i}" for i in range(8)
        ) + "\n\n" + experience + "## Technical Skills\n" + "、".join(
            f"skill{i}" for i in range(8, 13)
        ) + "\n"
        result = refine_markdown_skills(markdown, "工程师", client)
        self.assertIn(experience, result)
        self.assertIn("skill12、skill1", result)
        self.assertNotIn("Technical Skills", result)
        validate_markdown_skills(result)

    def test_packed_tags_cannot_bypass_limit(self) -> None:
        with self.assertRaisesRegex(ResumeResponseError, "13 项"):
            validate_generated_skills(["、".join(f"skill{i}" for i in range(13))])

    def test_certificates_and_generic_labels_are_rejected_even_in_short_lists(self) -> None:
        for label in ("CET-6", "英语六级", "VibeCoding", "软件开发", "工程问题建模"):
            with self.subTest(label=label), self.assertRaises(ResumeResponseError):
                validate_generated_skills(["Java", label])

    def test_specialist_skills_are_not_globally_blacklisted(self) -> None:
        validate_generated_skills(["模式识别", "数字信号处理", "自动控制原理", "PyTorch"])
        validate_generated_skills(["CI/CD", "C++", "C#", ".NET"])
        validate_generated_skills([])

    def test_supporting_tools_require_explicit_job_relevance(self) -> None:
        for label in ("Hutool", "Lombok", "日志：Logback", "CompletableFuture"):
            with self.subTest(label=label), self.assertRaises(ResumeResponseError):
                validate_generated_skills(["Java", label], "Java 后端开发工程师")
        validate_generated_skills(["Java", "Logback"], "Java 工程师，要求使用 Logback 排查日志问题")

    def test_markdown_lists_and_multiple_skill_sections_share_limit(self) -> None:
        for title in ("专业技能", "**专业技能**", "Technical Skills"):
            markdown = f"# 候选人\n\n## {title}\n" + "\n".join(
                f"- skill{i}" for i in range(8)
            ) + "\n\n## 核心技能\n" + "、".join(f"other{i}" for i in range(5))
            with self.subTest(title=title), self.assertRaises(ResumeResponseError):
                validate_markdown_skills(markdown)

    def test_other_sections_are_not_counted_or_rewritten(self) -> None:
        validate_markdown_skills(
            "# 候选人\n\n## 专业技能\nJava、Redis\n\n## 证书\nCET-6\n\n"
            "## 项目经历\n" + "、".join(f"dependency{i}" for i in range(20))
        )
