import unittest

from mail_messages import (
    IncomingMail,
    extract_code_for_rule,
    extract_mail_result,
    extract_verification_code,
    html_to_text,
    normalize_mail_conditions,
)


SENDERS = {"noreply@tm.openai.com", "noreply@tm1.openai.com"}


class MailMessageTests(unittest.TestCase):
    def test_extracts_english_chatgpt_code(self):
        message = IncomingMail(
            message_id="one",
            sender="noreply@tm.openai.com",
            subject="Your temporary ChatGPT login code",
            body="Your login code is 123456.",
        )
        self.assertEqual(extract_verification_code(message, SENDERS), "123456")

    def test_extracts_chinese_code_from_html(self):
        message = IncomingMail(
            message_id="two",
            sender="noreply@tm1.openai.com",
            subject="你的临时 ChatGPT 登录代码",
            body="<p>你的验证码是 <strong>876543</strong></p>",
        )
        self.assertEqual(extract_verification_code(message, SENDERS), "876543")

    def test_rejects_untrusted_sender(self):
        message = IncomingMail(
            message_id="three",
            sender="attacker@example.com",
            subject="Your temporary ChatGPT login code",
            body="Your login code is 123456.",
        )
        self.assertIsNone(extract_verification_code(message, SENDERS))

    def test_rejects_unrelated_mail(self):
        message = IncomingMail(
            message_id="four",
            sender="noreply@tm.openai.com",
            subject="OpenAI receipt 123456",
            body="Thank you for your purchase.",
        )
        self.assertIsNone(extract_verification_code(message, SENDERS))

    def test_html_to_text_ignores_script_content(self):
        self.assertEqual(html_to_text("<p>Hello</p><script>123456</script>"), "Hello")

    def test_custom_rule_matches_sender_and_subject(self):
        message = IncomingMail(
            message_id="five",
            sender="NO-REPLY@SILICONFLOW.CN",
            subject="Siliconflow 邮箱验证",
            body="您的验证码是 246810",
        )
        rule = {
            "senders": ["no-reply@siliconflow.cn"],
            "subject": "Siliconflow 邮箱验证",
            "code_pattern": r"(?<!\d)(\d{4,8})(?!\d)",
        }
        self.assertEqual(extract_code_for_rule(message, rule), "246810")

    def test_custom_rule_requires_all_filled_conditions_by_default(self):
        message = IncomingMail(
            message_id="six",
            sender="no-reply@siliconflow.cn",
            subject="其他通知",
            body="您的验证码是 135790",
        )
        rule = {
            "senders": ["no-reply@siliconflow.cn"],
            "subject": "Siliconflow 邮箱验证",
        }
        self.assertIsNone(extract_code_for_rule(message, rule))

    def test_custom_rule_domain_sender_mode_matches_any_local_part(self):
        message = IncomingMail(
            message_id="domain",
            sender="alerts@siliconflow.cn",
            subject="邮箱验证",
            body="验证码：246810",
        )
        rule = {
            "sender_mode": "domain",
            "senders": ["siliconflow.cn"],
            "subject": "邮箱验证",
        }
        self.assertEqual(extract_code_for_rule(message, rule), "246810")

    def test_dynamic_anthropic_sender_extracts_sign_in_link(self):
        message = IncomingMail(
            message_id="seven",
            sender="no-reply-12Y-Ka7gf0zzbXhoLnLgXg@mail.anthropic.com",
            subject="Sign in to Claude.ai",
            body='<a href="https://claude.ai/login?token=abc">Sign in</a>',
        )
        rule = {
            "sender_mode": "regex",
            "sender_pattern": r"no-reply(?:-[A-Za-z0-9]+)+@mail\.anthropic\.com",
            "subject": "Sign in to Claude",
            "extract_type": "link",
            "link_text_pattern": r"^\s*sign\s+in\s*$",
            "allowed_hosts": ["claude.ai"],
        }
        self.assertEqual(
            extract_mail_result(message, rule),
            {"type": "link", "value": "https://claude.ai/login?token=abc"},
        )

    def test_link_rule_rejects_untrusted_host_and_ambiguous_buttons(self):
        message = IncomingMail(
            message_id="eight",
            sender="no-reply@mail.anthropic.com",
            subject="Sign in to Claude.ai",
            body='<a href="https://evil.example/sign">Sign in</a>',
        )
        rule = {
            "senders": ["no-reply@mail.anthropic.com"],
            "extract_type": "link",
            "link_text_pattern": r"sign\s+in",
            "allowed_hosts": ["claude.ai"],
        }
        self.assertIsNone(extract_mail_result(message, rule))

    def test_advanced_conditions_support_all_operators_and_case_sensitivity(self):
        message = IncomingMail(
            message_id="nine",
            sender="no-reply@siliconflow.cn",
            subject="Siliconflow 邮箱验证",
            body="<p>您的验证码是 246810</p>",
        )
        base = {
            "senders": ["no-reply@siliconflow.cn"],
            "code_pattern": r"(?<!\d)(\d{4,8})(?!\d)",
            "conditions": [
                {"field": "subject", "operator": "equals", "value": "Siliconflow 邮箱验证"},
                {"field": "body", "operator": "regex", "value": r"验证码\s*是\s*\d+"},
                {"field": "body", "operator": "not_contains", "value": "失效"},
            ],
            "condition_logic": "all",
        }
        self.assertEqual(extract_code_for_rule(message, base), "246810")

        case_sensitive = {
            "senders": ["no-reply@siliconflow.cn"],
            "conditions": [
                {
                    "field": "subject",
                    "operator": "starts_with",
                    "value": "siliconflow",
                    "case_sensitive": True,
                }
            ],
        }
        self.assertIsNone(extract_code_for_rule(message, case_sensitive))
        case_insensitive = {**case_sensitive, "conditions": [{**case_sensitive["conditions"][0], "case_sensitive": False}]}
        self.assertEqual(extract_code_for_rule(message, case_insensitive), "246810")

    def test_advanced_conditions_any_and_negative_regex(self):
        message = IncomingMail(
            message_id="ten",
            sender="no-reply@siliconflow.cn",
            subject="安全提醒",
            body="验证码：135790",
        )
        rule = {
            "senders": ["no-reply@siliconflow.cn"],
            "conditions": [
                {"field": "subject", "operator": "contains", "value": "邮箱验证"},
                {"field": "body", "operator": "not_regex", "value": r"过期\s*验证码"},
            ],
            "condition_logic": "any",
        }
        self.assertEqual(extract_code_for_rule(message, rule), "135790")

    def test_advanced_contains_condition_supports_multiple_keywords(self):
        message = IncomingMail(
            message_id="eleven",
            sender="no-reply@siliconflow.cn",
            subject="Siliconflow 邮箱验证",
            body="验证码：135790",
        )
        rule = {
            "senders": ["no-reply@siliconflow.cn"],
            "conditions": [
                {
                    "field": "subject",
                    "operator": "contains",
                    "value": "Siliconflow,邮箱验证",
                    "value_mode": "all",
                }
            ],
        }
        self.assertEqual(extract_code_for_rule(message, rule), "135790")
        rule["conditions"][0]["value_mode"] = "any"
        rule["conditions"][0]["value"] = "Siliconflow,不存在"
        self.assertEqual(extract_code_for_rule(message, rule), "135790")

    def test_advanced_condition_validation(self):
        with self.assertRaises(ValueError):
            normalize_mail_conditions([{"field": "from", "operator": "contains", "value": "x"}])
        with self.assertRaises(ValueError):
            normalize_mail_conditions([{"field": "body", "operator": "regex", "value": "("}])
        with self.assertRaises(ValueError):
            normalize_mail_conditions([{"field": "body", "operator": "contains", "value": "x", "case_sensitive": "maybe"}])


if __name__ == "__main__":
    unittest.main()
