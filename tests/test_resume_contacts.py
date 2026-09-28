"""Regression coverage for contact boundaries lost in PDF extraction."""

import unittest

from pbl_jobs_finder.modules.resume_contacts import normalize_resume_contacts


class ResumeContactTests(unittest.TestCase):
    def test_joined_contact_and_markdown_escape_are_repaired(self) -> None:
        for label in ("", "邮箱：", "电子邮箱：", "电话：", "Email: "):
            for at_sign in ("@", "\\@"):
                with self.subTest(label=label, at_sign=at_sign):
                    original = f"{label}19883177095617213477{at_sign}qq.com | 杭州"
                    fixed = normalize_resume_contacts(original)
                    self.assertEqual(fixed, "电话：19883177095 | 邮箱：617213477@qq.com | 杭州")
                    self.assertEqual(normalize_resume_contacts(fixed), fixed)

    def test_ordinary_addresses_and_unrelated_numbers_are_preserved(self) -> None:
        for text in (
            "19883177095@qq.com",
            "617213477@qq.com",
            "19883177095617213477@example.com",
            "user19883177095617213477@qq.com",
            "user+19883177095617213477@qq.com",
            "19883177095617213477@qq.com.example.org",
            "19883177095617213477",
            "电话：19883177095 | 邮箱：617213477@qq.com",
        ):
            with self.subTest(text=text):
                self.assertEqual(normalize_resume_contacts(text), text)
