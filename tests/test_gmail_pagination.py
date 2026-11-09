import importlib.util
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from googleapiclient.errors import HttpError
from httplib2 import Response


def gmail_error(status):
    return HttpError(Response({"status": str(status)}), b"{}")


def history_page(cursor, *ids, next_page=None):
    return {
        "historyId": str(cursor),
        "history": [{"messagesAdded": [{"message": {"id": message_id}} for message_id in ids]}],
        "nextPageToken": next_page,
    }


class StopAfterPolls:
    def __init__(self, remaining):
        self.remaining = remaining

    def is_set(self):
        return self.remaining == 0

    def wait(self, seconds):
        self.remaining -= 1


class GmailPaginationTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        source = Path(__file__).resolve().parents[1] / "mailapi_shared" / "application.py"
        spec = importlib.util.spec_from_file_location("mailapi_shared.gmail_pagination_test", source)
        self.app = importlib.util.module_from_spec(spec)
        logger = logging.getLogger("mailapi.test.errors")
        with patch.object(logger, "handlers", []), patch.dict(os.environ, {
            "MAILAPI_MODE": "test", "TEST_DATA_DIR": directory, "TEST_EXTERNAL_WATCHERS": "0",
        }):
            spec.loader.exec_module(self.app)
            self.addCleanup(logger.handlers[0].close)
        self.cfg = {"name": "account-1", "email": "gmail@example.com"}
        self.service = Mock()
        self.users = self.service.users.return_value
        self.unread = self.users.messages.return_value.list
        self.unread.return_value.execute.return_value = {}
        self.history = self.users.history.return_value.list
        self.enterContext(patch.object(self.app, "get_gmail_service", return_value=self.service))
        self.enterContext(patch.object(self.app, "log_exception"))
        self.process = self.enterContext(patch.object(self.app, "process_message", side_effect=self.message_result))
        self.mark = self.enterContext(patch.object(self.app, "mark_as_read"))
        self.app.write_history_id(self.cfg, 100)

    def message_result(self, service, cfg, message_id):
        if message_id == "deleted":
            raise gmail_error(404)
        return {"message_id": message_id, "code": "123456", "subject": "Synthetic code"}

    def saved_ids(self):
        return [item["message_id"] for item in self.app.mailbox_history(self.cfg)]

    def test_incremental_pages_retry_from_original_cursor_and_skip_deleted_message(self):
        def page(**params):
            self.assertEqual(self.app.read_history_id(self.cfg), 100)
            if not params.get("pageToken"):
                return Mock(execute=Mock(return_value=history_page(140, "first", next_page="p2")))
            if self.history.call_count == 2:
                return Mock(execute=Mock(side_effect=gmail_error(503)))
            return Mock(execute=Mock(return_value=history_page(142, "deleted", "second")))

        self.history.side_effect = page
        self.app.watch_gmail_account(self.cfg, StopAfterPolls(2))

        self.assertEqual([call.kwargs["startHistoryId"] for call in self.history.call_args_list], [100] * 4)
        self.assertEqual([call.kwargs.get("pageToken") for call in self.history.call_args_list], [None, "p2"] * 2)
        self.assertEqual(self.app.read_history_id(self.cfg), 142)
        self.assertEqual(self.saved_ids(), ["second", "first"])
        self.users.getProfile.assert_not_called()

    def test_unread_scan_reads_each_page_before_marking_messages_read(self):
        def page(**params):
            self.mark.assert_not_called()
            if params.get("pageToken") == "p2":
                return Mock(execute=Mock(return_value={"messages": [{"id": "first"}, {"id": "second"}]}))
            return Mock(execute=Mock(return_value={"messages": [{"id": "first"}], "nextPageToken": "p2"}))

        self.unread.side_effect = page
        self.app.scan_unread_once(self.service, self.cfg)

        self.assertEqual(self.unread.call_args_list[1].kwargs["pageToken"], "p2")
        self.assertEqual([call.args[-1] for call in self.mark.call_args_list], ["first", "second"])
        self.assertEqual(self.saved_ids(), ["second", "first"])

    def test_expired_cursor_uses_baseline_before_rescan_and_retries_failed_catchup(self):
        events = []
        baselines = iter([150, 160])

        def profile(**params):
            self.assertEqual(self.app.read_history_id(self.cfg), 100)
            cursor = next(baselines)
            events.append(("baseline", cursor))
            return Mock(execute=Mock(return_value={"historyId": str(cursor)}))

        def unread(**params):
            self.assertEqual(self.app.read_history_id(self.cfg), 100)
            page_token = params.get("pageToken")
            events.append(("unread", page_token))
            if self.unread.call_count == 1:
                return Mock(execute=Mock(return_value={}))
            if not page_token:
                return Mock(execute=Mock(return_value={"nextPageToken": "p2"}))
            if self.unread.call_count == 3:
                return Mock(execute=Mock(side_effect=gmail_error(503)))
            return Mock(execute=Mock(return_value={"messages": [{"id": "catchup"}]}))

        def history(**params):
            cursor = params["startHistoryId"]
            events.append(("history", cursor))
            if cursor == 100:
                return Mock(execute=Mock(side_effect=gmail_error(404)))
            self.assertEqual(self.app.read_history_id(self.cfg), 160)
            return Mock(execute=Mock(return_value=history_page(170, "during-catchup")))

        self.users.getProfile.side_effect = profile
        self.unread.side_effect = unread
        self.history.side_effect = history
        self.app.watch_gmail_account(self.cfg, StopAfterPolls(3))

        self.assertEqual(events, [
            ("unread", None), ("history", 100),
            ("baseline", 150), ("unread", None), ("unread", "p2"),
            ("baseline", 160), ("unread", None), ("unread", "p2"), ("history", 160),
        ])
        self.assertEqual(self.app.read_history_id(self.cfg), 170)
        self.assertEqual(self.saved_ids(), ["during-catchup", "catchup"])
