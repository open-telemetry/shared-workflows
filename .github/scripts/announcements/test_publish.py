import json
from http.client import IncompleteRead
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs

import publish


ANNOUNCEMENT = {
    "title": "New OpenTelemetry blog post",
    "text": "Read our latest news.",
    "url": "https://opentelemetry.io/blog/2026/news/",
}
TARGETS = {
    "linkedin": ["linkedin-id"], "bluesky": ["bluesky-id"],
    "mastodon": ["mastodon-id"], "slack": ["C123", "C456"],
    "groupsio": [123, 456],
}
WEBHOOK = "https://hooks.slack.com/triggers/T000/123/test"
CREDS = {
    **{secret: "test-value" for secrets in publish.SECRETS.values() for secret in secrets},
    "SLACK_WEBHOOK_URL": WEBHOOK,
}


def plan(channels=None, mode="publish", announcement=None, targets=None):
    return publish.build_plan(
        ANNOUNCEMENT if announcement is None else announcement,
        list(TARGETS) if channels is None else channels,
        TARGETS if targets is None else targets, mode,
    )


def publisher(credentials=None):
    return publish.Publisher(
        CREDS if credentials is None else credentials,
    )


def buffer_channel(channel="linkedin", target="linkedin-id"):
    return {"data": {"channel": {
        "id": target, "service": channel, "isLocked": False, "isDisconnected": False,
    }}}


class PlanTests(unittest.TestCase):
    def test_selected_channels_and_multiple_destinations(self):
        items = plan(["slack", "groupsio"])
        self.assertEqual([item["target"] for item in items], ["C123", "C456", 123, 456])
        self.assertTrue(all(item["action"] == "publish" for item in items))
        self.assertEqual(items[0]["text"], "\n\n".join(ANNOUNCEMENT.values()))
        self.assertEqual(items[2]["text"], ANNOUNCEMENT["text"] + "\n\n" + ANNOUNCEMENT["url"])

    def test_same_structure_for_every_channel(self):
        items = {item["channel"]: item for item in plan(mode="check")}
        social = ANNOUNCEMENT["text"] + "\n\n" + ANNOUNCEMENT["url"]
        for channel in ["linkedin", "bluesky", "mastodon", "groupsio"]:
            self.assertEqual(items[channel]["text"], social)
        self.assertEqual(items["slack"]["text"], ANNOUNCEMENT["title"] + "\n\n" + social)
        self.assertEqual(items["groupsio"]["title"], ANNOUNCEMENT["title"])

    def test_channel_limits_include_the_url(self):
        room = 300 - len("\n\n" + ANNOUNCEMENT["url"])
        self.assertEqual(len(plan(["bluesky"], announcement={**ANNOUNCEMENT, "text": "a" * room})), 1)
        for channel, limit in [("bluesky", 300), ("mastodon", 500), ("linkedin", 3000)]:
            with self.subTest(channel=channel), self.assertRaisesRegex(
                publish.DeliveryError, f"{channel} message is .* the limit is {limit}"
            ):
                plan([channel], announcement={**ANNOUNCEMENT, "text": "a" * limit})
        # Overrides shorten one channel without affecting the others.
        long_text = {**ANNOUNCEMENT, "text": "a" * 400, "overrides": {"bluesky": {"text": "short"}}}
        self.assertEqual(len(plan(["bluesky", "mastodon"], announcement=long_text)), 2)

    def test_check_mode_only_previews(self):
        self.assertTrue(all(item["action"] == "preview" for item in plan(mode="check")))
        self.assertEqual(len(plan(mode="check", targets={})), 5)
        items = plan(["bluesky", "slack"], mode="check", targets={})
        self.assertEqual([(item["channel"], item["target"]) for item in items],
                         [("bluesky", None), ("slack", None)])

    def test_overrides(self):
        announcement = {**ANNOUNCEMENT, "overrides": {
            "bluesky": {"text": "Short social copy"}, "groupsio": {"title": "List subject"},
        }}
        items = plan(["bluesky", "groupsio"], announcement=announcement)
        self.assertTrue(items[0]["text"].startswith("Short social copy\n\n"))
        self.assertEqual(items[1]["title"], "List subject")

    def test_invalid_configuration(self):
        cases = [
            {"channels": []},
            {"channels": ["mastadon"]},
            {"channels": ["slack", "slack"]},
            {"channels": [None]},
            {"channels": ["slack"], "targets": {}},
            {"channels": ["groupsio"], "targets": {"groupsio": [True]}},
            {"channels": ["groupsio"], "targets": {"groupsio": ["123"]}},
            {"channels": ["slack"], "targets": {"slack": ["general"]}},
            {"channels": ["slack"], "targets": {"slack": ["#announcements"]}},
            {"channels": ["slack"], "targets": {"slack": ["C123", "C123"]}},
            {"channels": ["reddit"]},
            {"channels": ["linkedin"], "targets": {"linkedin": ["abc", "ABC"]}},
            {"channels": ["linkedin", "bluesky"], "targets": {"linkedin": ["same"], "bluesky": ["same"]}},
            {"mode": "drafft"},
            {"mode": "dry-run"},
            {"announcement": {**ANNOUNCEMENT, "title": ""}},
            {"announcement": {**ANNOUNCEMENT, "title": "a" * 301}},
            {"announcement": {**ANNOUNCEMENT, "text": "\ud800"}},
            {"announcement": {**ANNOUNCEMENT, "url": "http://opentelemetry.io"}},
            {"announcement": {**ANNOUNCEMENT, "url": "https://user:password@example.com"}},
            {"announcement": {**ANNOUNCEMENT, "url": "https://["}},
            {"announcement": {**ANNOUNCEMENT, "url": "https://example.com/\n"}},
            {"announcement": {**ANNOUNCEMENT, "overrides": {"slack": {"url": "https://example.com"}}}},
            {"announcement": {**ANNOUNCEMENT, "overrides": {"slack": {"text": ""}}}},
            {"channels": ["slack"], "targets": {"slack": [f"C{i}" for i in range(21)]}},
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(publish.DeliveryError):
                plan(**case)

    @patch("publish.request")
    def test_check_never_makes_requests_or_requires_secrets(self, request):
        items = plan(mode="check")
        self.assertTrue(publisher({}).run(items))
        self.assertTrue(all(item["status"] == "preview" for item in items))
        request.assert_not_called()

    @patch("publish.request")
    def test_draft_slack_preview_without_credentials(self, request):
        items = plan(["slack"], mode="draft")
        self.assertTrue(publisher({}).run(items))
        self.assertTrue(all(item["status"] == "preview" for item in items))
        request.assert_not_called()

    @patch("publish.request")
    def test_mixed_draft_run_never_publishes(self, request):
        request.side_effect = [
            buffer_channel("linkedin", "linkedin-id"),
            buffer_channel("bluesky", "bluesky-id"),
            buffer_channel("mastodon", "mastodon-id"),
            *[{"data": {"createPost": {"post": {"id": f"post-{index}", "status": "draft"}}}}
              for index in range(3)],
            {"object": "draft", "id": 12}, {"object": "draft", "id": 12},
            {"object": "draft", "id": 13}, {"object": "draft", "id": 13},
        ]
        items = plan(mode="draft")
        credentials = {"BUFFER_API_KEY": "buffer", "GROUPSIO_API_KEY": "groupsio"}
        self.assertTrue(publisher(credentials).run(items))
        self.assertEqual(sum(item["status"] == "drafted" for item in items), 5)
        self.assertEqual(sum(item["status"] == "preview" for item in items), 2)
        for call in request.call_args_list:
            self.assertNotIn("slack", call.args[0])
            self.assertFalse(call.args[0].endswith("/postdraft"))
            if "variables" in call.args[1] and "input" in call.args[1]["variables"]:
                self.assertTrue(call.args[1]["variables"]["input"]["saveToDraft"])

    @patch("publish.request")
    def test_missing_secret_prevents_all_requests(self, request):
        with self.assertRaisesRegex(publish.DeliveryError, "SLACK_WEBHOOK_URL"):
            publisher({"BUFFER_API_KEY": "test"}).run(plan(["linkedin", "slack"]))
        request.assert_not_called()

    @patch("publish.request")
    def test_invalid_header_credentials_fail_without_exposing_them(self, request):
        with self.assertRaises(publish.DeliveryError) as failure:
            publisher({**CREDS, "SLACK_WEBHOOK_URL": "sensitive\nvalue"}).run(plan(["slack"]))
        self.assertIn("SLACK_WEBHOOK_URL", str(failure.exception))
        self.assertNotIn("sensitive", str(failure.exception))
        request.assert_not_called()

    @patch("publish.request")
    def test_slack_webhook_must_be_a_slack_url(self, request):
        for url in ["https://example.com/hook", "http://hooks.slack.com/services/x",
                    "https://hooks.slack.com.example.com/x",
                    "https://hooks.slack.com/services/T000/B000/x"]:
            with self.subTest(url=url), self.assertRaises(publish.DeliveryError) as failure:
                publisher({**CREDS, "SLACK_WEBHOOK_URL": url}).run(plan(["slack"]))
            self.assertNotIn(url, str(failure.exception))
        request.assert_not_called()

    def test_groupsio_host_is_allowlisted(self):
        with self.assertRaises(publish.DeliveryError):
            publish.Publisher(CREDS, groupsio_host="example.com")


class DeliveryTests(unittest.TestCase):
    @patch("publish.request")
    def test_buffer_draft_and_publish_contracts(self, request):
        for mode, status, sharing in [("draft", "draft", "addToQueue"), ("publish", "sending", "shareNow")]:
            with self.subTest(mode=mode):
                request.reset_mock()
                request.side_effect = [
                    buffer_channel(),
                    {"data": {"createPost": {"post": {"id": "post-id", "status": status}}}},
                ]
                items = plan(["linkedin"], mode)
                self.assertTrue(publisher().run(items))
                body = request.call_args.args[1]["variables"]["input"]
                self.assertEqual(body["mode"], sharing)
                self.assertEqual(body["saveToDraft"], mode == "draft")
                self.assertEqual(items[0]["remote_id"], "post-id")

    @patch("publish.request")
    def test_buffer_service_mismatch_prevents_all_writes(self, request):
        request.return_value = buffer_channel("bluesky")
        with self.assertRaisesRegex(publish.DeliveryError, "does not match"):
            publisher().run(plan(["slack", "linkedin"]))
        self.assertEqual(request.call_count, 1)
        self.assertIn("query", request.call_args.args[1]["query"])

    @patch("publish.request")
    def test_buffer_validates_all_channels_before_writes(self, request):
        request.side_effect = [buffer_channel(), buffer_channel("linkedin", "bluesky-id")]
        with self.assertRaises(publish.DeliveryError):
            publisher().run(plan(["linkedin", "bluesky"]))
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all("query" in call.args[1]["query"] for call in request.call_args_list))

    @patch("publish.request")
    def test_buffer_locked_or_disconnected_fails_before_writes(self, request):
        for field in ["isLocked", "isDisconnected"]:
            with self.subTest(field=field):
                data = buffer_channel()
                data["data"]["channel"][field] = True
                request.return_value = data
                with self.assertRaises(publish.DeliveryError):
                    publisher().run(plan(["linkedin"]))

    @patch("publish.request")
    def test_buffer_graphql_and_mutation_errors_fail(self, request):
        for error in [
            {"errors": [{"message": "invalid query"}]},
            {"data": {"createPost": {"message": "Too long"}}},
            {"data": {"createPost": {"post": {"id": "post-id", "status": "error"}}}},
        ]:
            with self.subTest(error=error):
                request.side_effect = [buffer_channel(), error]
                items = plan(["linkedin"])
                self.assertFalse(publisher().run(items))
                self.assertEqual(items[0]["status"], "failed")

    @patch("publish.request")
    def test_slack_posts_to_each_selected_channel(self, request):
        request.side_effect = ['{"ok":true}', "ok"]
        items = plan(["slack"], targets={"slack": ["C123", "C456"]},
                     announcement={**ANNOUNCEMENT, "text": "Hi <!channel> & <@U123>"})
        self.assertTrue(publisher().run(items))
        self.assertEqual([call.args[0] for call in request.call_args_list], [WEBHOOK, WEBHOOK])
        self.assertEqual([call.args[1]["channel"] for call in request.call_args_list],
                         ["C123", "C456"])
        self.assertTrue(request.call_args.kwargs["raw"])
        payload = request.call_args.args[1]
        self.assertEqual(set(payload), {"channel", "text"})
        self.assertIn("\n\nHi &lt;!channel&gt; &amp; &lt;@U123&gt;\n\n", payload["text"])
        self.assertNotIn("remote_id", items[0])

    @patch("publish.request")
    def test_partial_failure_is_reported_and_not_retried(self, request):
        request.side_effect = ['{"ok":false,"error":"channel_not_found"}', "ok"]
        items = plan(["slack"])
        progress = MagicMock()
        self.assertFalse(publisher().run(items, progress))
        self.assertEqual([item["status"] for item in items], ["failed", "submitted"])
        self.assertIn("channel_not_found", items[0]["error"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(progress.call_count, 2)

    @patch("publish.request")
    def test_groupsio_native_draft_and_publish(self, request):
        for mode in ["draft", "publish"]:
            with self.subTest(mode=mode):
                request.reset_mock()
                request.side_effect = [{"object": "draft", "id": 12}, {"object": "draft", "id": 12}, None]
                items = plan(["groupsio"], mode, targets={"groupsio": [123]},
                             announcement={**ANNOUNCEMENT, "text": "<script>& news"})
                self.assertTrue(publisher().run(items))
                calls = request.call_args_list
                self.assertEqual(calls[0].args[0], "https://lists.cncf.io/api/v1/newdraft")
                self.assertEqual(calls[0].args[1], {"group_id": 123, "draft_type": "draft_type_post"})
                self.assertEqual(calls[1].args[0], "https://lists.cncf.io/api/v1/updatedraft")
                self.assertIn("&lt;script&gt;&amp;", calls[1].args[1]["body"])
                self.assertEqual(len(calls), 2 if mode == "draft" else 3)
                if mode == "publish":
                    self.assertTrue(calls[2].kwargs["empty_ok"])
                    self.assertTrue(calls[2].args[0].endswith("/postdraft"))

    @patch("publish.request")
    def test_unexpected_error_is_recorded_and_other_destinations_continue(self, request):
        request.side_effect = [KeyError("secret detail"), "ok"]
        items = plan(["slack"])
        self.assertFalse(publisher().run(items))
        self.assertEqual([item["status"] for item in items], ["failed", "submitted"])
        self.assertIn("Unexpected KeyError; delivery may have occurred", items[0]["error"])
        self.assertNotIn("secret", items[0]["error"])

    @patch("publish.request")
    def test_failed_groupsio_update_leaves_draft_id_and_never_posts(self, request):
        request.side_effect = [{"object": "draft", "id": 12}, {"object": "error", "type": "bad_request"}]
        items = plan(["groupsio"], targets={"groupsio": [123]})
        self.assertFalse(publisher().run(items))
        self.assertEqual(items[0]["remote_id"], 12)
        self.assertEqual(items[0]["remote_status"], "empty-draft")
        self.assertEqual(request.call_count, 2)


class TransportTests(unittest.TestCase):
    @patch("publish.build_opener")
    def test_json_and_form_encoding(self, opener):
        response = opener.return_value.open.return_value.__enter__.return_value
        response.read.return_value = b'{"ok": true}'
        publish.request("https://example.com", {"text": "a & b"}, {"Authorization": "Bearer test"})
        req = opener.return_value.open.call_args.args[0]
        self.assertEqual(json.loads(req.data), {"text": "a & b"})
        self.assertEqual(req.get_header("Authorization"), "Bearer test")
        self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 30)
        publish.request("https://example.com", {"text": "a & b"}, form=True)
        req = opener.return_value.open.call_args.args[0]
        self.assertEqual(parse_qs(req.data.decode()), {"text": ["a & b"]})

    @patch("publish.build_opener")
    def test_raw_response_and_webhook_error_codes(self, opener):
        response = opener.return_value.open.return_value.__enter__.return_value
        response.read.return_value = b"ok"
        self.assertEqual(publish.request(WEBHOOK, {}, raw=True), "ok")
        error = HTTPError(WEBHOOK, 404, "Not Found", {}, None)
        error.read = MagicMock(return_value=b"channel_not_found")
        opener.return_value.open.side_effect = error
        with self.assertRaisesRegex(publish.DeliveryError, r"HTTP 404 \(channel_not_found\)") as failure:
            publish.request(WEBHOOK, {}, raw=True)
        self.assertNotIn(WEBHOOK, str(failure.exception))

    @patch("publish.build_opener")
    def test_server_errors_may_have_delivered(self, opener):
        for code, uncertain in [(400, False), (503, True)]:
            with self.subTest(code=code):
                opener.return_value.open.side_effect = HTTPError("https://example.com", code, "", {}, None)
                with self.assertRaises(publish.DeliveryError) as failure:
                    publish.request("https://example.com", {})
                self.assertEqual("may have occurred" in str(failure.exception), uncertain)

    @patch("publish.build_opener")
    def test_empty_response_only_allowed_explicitly(self, opener):
        opener.return_value.open.return_value.__enter__.return_value.read.return_value = b""
        self.assertIsNone(publish.request("https://example.com", {}, empty_ok=True))
        with self.assertRaises(publish.DeliveryError):
            publish.request("https://example.com", {})

    @patch("publish.build_opener")
    def test_invalid_response_shapes_are_explicit_errors(self, opener):
        response = opener.return_value.open.return_value.__enter__.return_value
        for body in [b"not json", b"[]", b"null"]:
            with self.subTest(body=body):
                response.read.return_value = body
                with self.assertRaises(publish.DeliveryError):
                    publish.request("https://example.com", {})

    @patch("publish.build_opener")
    def test_transport_errors_not_retried_or_leaked(self, opener):
        for error in [
            HTTPError("https://example.com", 429, "secret response", {}, None),
            URLError("secret credential"), TimeoutError("secret credential"),
            IncompleteRead(b"secret partial response"),
        ]:
            with self.subTest(error=error):
                opener.return_value.open.reset_mock()
                opener.return_value.open.side_effect = error
                with self.assertRaises(publish.DeliveryError) as failure:
                    publish.request("https://example.com", {})
                self.assertNotIn("secret", str(failure.exception))
                self.assertEqual(opener.return_value.open.call_count, 1)

    def test_redirects_are_rejected(self):
        self.assertIsNone(publish.NoRedirects().redirect_request(None, None, 302, "", {}, "https://other.com"))


class WaitTests(unittest.TestCase):
    def test_waits_until_200(self):
        status = MagicMock(side_effect=[404, None, 301, 200])
        sleep = MagicMock()
        publish.wait_until_live(ANNOUNCEMENT["url"], 5, status=status, sleep=sleep,
                                clock=MagicMock(return_value=0))
        self.assertEqual(status.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [30, 30, 30])

    def test_gives_up_after_deadline(self):
        clock = MagicMock(side_effect=[0, 30, 45, 60])
        sleep = MagicMock()
        with self.assertRaisesRegex(publish.DeliveryError, "not live after 1 minutes .*404"):
            publish.wait_until_live(ANNOUNCEMENT["url"], 1, status=MagicMock(return_value=404),
                                    sleep=sleep, clock=clock)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [30, 15])

    @patch("publish.build_opener")
    def test_status_does_not_follow_redirects(self, opener):
        opener.return_value.open.side_effect = HTTPError(ANNOUNCEMENT["url"], 301, "", {}, None)
        self.assertEqual(publish.url_status(ANNOUNCEMENT["url"]), 301)
        self.assertIsInstance(opener.call_args.args[0], publish.NoRedirects)
        opener.return_value.open.side_effect = URLError("down")
        self.assertIsNone(publish.url_status(ANNOUNCEMENT["url"]))

    def test_wait_minutes_validation(self):
        self.assertEqual(publish.wait_minutes("0"), 0)
        self.assertEqual(publish.wait_minutes("120"), 120)
        for value in ["", "-1", "121", "1.5", "ten"]:
            with self.subTest(value=value), self.assertRaises(publish.DeliveryError):
                publish.wait_minutes(value)


class MainTests(unittest.TestCase):
    @patch("publish.wait_until_live")
    @patch("publish.request")
    def test_check_stage_validates_renders_and_waits(self, request, wait):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary"
            env = {
                "ANNOUNCEMENT_STAGE": "check", "ANNOUNCEMENT_MODE": "draft",
                "ANNOUNCEMENT_WAIT_MINUTES": "30",
                "ANNOUNCEMENT": json.dumps(ANNOUNCEMENT),
                "ANNOUNCEMENT_CHANNELS": json.dumps(list(TARGETS)),
                "ANNOUNCEMENT_TARGETS": json.dumps(TARGETS),
                "GITHUB_STEP_SUMMARY": str(summary),
            }
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(publish.main(), 0)
            wait.assert_called_once_with(ANNOUNCEMENT["url"], 30)
            self.assertIn(ANNOUNCEMENT["text"], summary.read_text())
            self.assertEqual(list(Path(directory).iterdir()), [summary])
            env["ANNOUNCEMENT"] = json.dumps({**ANNOUNCEMENT, "text": "a" * 500})
            wait.reset_mock()
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(publish.main(), 1)
            wait.assert_not_called()
            self.assertIn("the limit is 300", summary.read_text())
        request.assert_not_called()

    @patch("publish.request")
    def test_cli_persists_check_report_and_summary(self, request):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary"
            env = {
                "ANNOUNCEMENT_REPORT_DIR": directory,
                "ANNOUNCEMENT": json.dumps(ANNOUNCEMENT),
                "ANNOUNCEMENT_CHANNELS": json.dumps(list(TARGETS)),
                "ANNOUNCEMENT_TARGETS": json.dumps(TARGETS),
                "GITHUB_STEP_SUMMARY": str(summary),
            }
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(publish.main(), 0)
            report = json.loads((Path(directory) / "report.json").read_text())
            self.assertTrue(report["success"])
            self.assertEqual(len(report["deliveries"]), 7)
            for item in report["deliveries"]:
                self.assertLessEqual(set(item), set(publish.REPORT_FIELDS))
            self.assertNotIn(ANNOUNCEMENT["text"], json.dumps(report))
            self.assertIn("preview: 7", summary.read_text())
            self.assertIn(ANNOUNCEMENT["text"], summary.read_text())
        request.assert_not_called()

    @patch("publish.request")
    def test_invalid_input_persists_failure_without_network(self, request):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {
                "ANNOUNCEMENT_REPORT_DIR": directory, "ANNOUNCEMENT": "invalid",
            }, clear=True):
                self.assertEqual(publish.main(), 1)
            report = json.loads((Path(directory) / "report.json").read_text())
            self.assertFalse(report["success"])
            self.assertIn("valid JSON", report["error"])
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
