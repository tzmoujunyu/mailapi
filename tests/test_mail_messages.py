import unittest

from mail_messages import IncomingMail, extract_code_for_rule, extract_verification_code, html_to_text


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


if __name__ == "__main__":
    unittest.main()
