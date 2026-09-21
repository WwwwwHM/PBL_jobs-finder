"""Versioned interview policy and question-plan tests."""

from __future__ import annotations

import unittest

from pbl_jobs_finder.policies.interview_policy import (
    DEFAULT_INTERVIEW_MODE,
    INTERVIEW_STANDARD_POLICY_VERSION,
    InterviewPolicyError,
    get_interview_mode_choices,
    get_interview_policy,
    normalize_feedback_mode,
)


class InterviewPolicyTests(unittest.TestCase):
    def test_reviewed_modes_have_bounded_deterministic_plans(self) -> None:
        policy = get_interview_policy()
        standard = policy.get_mode(DEFAULT_INTERVIEW_MODE)
        focused = policy.get_mode("focused_live")

        self.assertEqual(policy.version, INTERVIEW_STANDARD_POLICY_VERSION)
        self.assertEqual(
            (standard.question_count, standard.max_follow_up_count), (5, 3)
        )
        self.assertEqual((focused.question_count, focused.max_follow_up_count), (3, 1))
        plan = focused.build_question_plan()
        self.assertEqual([item["round"] for item in plan], [1, 2, 3])
        self.assertEqual(plan[0]["competency"], "experience_evidence")
        self.assertNotIn("question", plan[0])

    def test_unknown_modes_versions_and_feedback_are_rejected(self) -> None:
        policy = get_interview_policy()
        with self.assertRaisesRegex(InterviewPolicyError, "未知的面试模式"):
            policy.get_mode("unreviewed")
        with self.assertRaisesRegex(InterviewPolicyError, "未知的模拟面试策略版本"):
            get_interview_policy("interview-unknown-v1")
        with self.assertRaisesRegex(InterviewPolicyError, "未知的反馈方式"):
            normalize_feedback_mode("sometimes")

    def test_frontend_choices_come_from_versioned_policy(self) -> None:
        self.assertEqual(
            get_interview_mode_choices(),
            (("标准面试", "standard_live"), ("快速面试", "focused_live")),
        )


if __name__ == "__main__":
    unittest.main()
