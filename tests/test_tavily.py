"""Tavily integration tests with mock HTTP only; no API credits consumed."""
import copy
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
from bs4 import BeautifulSoup
import main

NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
SETTINGS = main.load_config(main.ROOT / "feeds.yaml")["settings"]


def row(url="https://x.com/dotey/status/123", **extra):
    return {"url": url, "title": "OCR workflow", "content": "<p>Visual document processing</p>", **extra}


class TavilyTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_payloads_dedup_and_unverified_time(self):
        calls = []

        def handler(request):
            self.assertEqual(str(request.url), "https://api.tavily.com/search")
            self.assertEqual(request.method, "POST")
            body = json.loads(request.content)
            calls.append(body)
            self.assertEqual(body["api_key"], "test-key")
            self.assertEqual(body["search_depth"], "basic")
            self.assertEqual(body["time_range"], "day")
            self.assertEqual(body["include_domains"], ["x.com", "twitter.com"])
            self.assertEqual(request.extensions["timeout"]["read"], 20)
            if body["max_results"] == 15:
                return httpx.Response(200, json={"results": [row(), row("https://x.com/levelsio"), row("https://x.com.evil.example/user/status/123")]})
            return httpx.Response(200, json={"results": [row("https://twitter.com/dotey/status/123?utm_source=x"),
                row("https://x.com/op7418/status/456", published_date="2026-11-17T11:00:00Z")]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
                articles, health = await main.fetch_tavily(client, NOW, SETTINGS)
        self.assertEqual({(b["query"], b["max_results"]) for b in calls}, {(q, n) for _, q, n in main.TAVILY_QUERIES})
        self.assertEqual(health["status"], "success")
        self.assertEqual(health["items"], 2)
        self.assertEqual(articles[0].published_at, "")
        self.assertEqual(articles[0].summary, "Visual document processing")
        self.assertEqual(articles[1].published_at, "2026-11-17T11:00:00+00:00")

    async def test_partial_failure_keeps_good_results_without_secret_logs(self):
        def handler(request):
            if json.loads(request.content)["max_results"] == 15:
                return httpx.Response(429, text="private-key-do-not-log")
            return httpx.Response(200, json={"results": [row()]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.dict(os.environ, {"TAVILY_API_KEY": "private-key-do-not-log"}), self.assertLogs(main.LOG, level="WARNING") as logs:
                articles, health = await main.fetch_tavily(client, NOW, SETTINGS)
        self.assertEqual(len(articles), 1)
        self.assertEqual(health["status"], "partial")
        self.assertNotIn("private-key-do-not-log", " ".join(logs.output))
        self.assertIn("429", health["reason"])

    async def test_missing_key_makes_no_requests(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("Unexpected network"))) as client:
            with patch.dict(os.environ, {"TAVILY_API_KEY": ""}):
                articles, health = await main.fetch_tavily(client, NOW, SETTINGS)
        self.assertEqual(articles, [])
        self.assertEqual(health["status"], "failed")

    async def test_timeout_and_malformed_response_are_isolated(self):
        def handler(request):
            if json.loads(request.content)["max_results"] == 15:
                raise httpx.ReadTimeout("timeout", request=request)
            return httpx.Response(200, json={"results": {}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
                articles, health = await main.fetch_tavily(client, NOW, SETTINGS)
        self.assertEqual(articles, [])
        self.assertEqual(health["status"], "failed")
        self.assertEqual(len(health["queries"]), 2)

    def test_explicit_old_future_and_invalid_dates(self):
        for date in ["2026-11-16T11:59:59Z", "2026-11-17T12:00:01Z"]:
            self.assertIsNone(main.parse_x_result(row(published_date=date), NOW, 1400))
        for date in ["2026-11-17", "bad-date", None]:
            self.assertEqual(main.parse_x_result(row(published_date=date), NOW, 1400).published_at, "")

    async def test_merge_numbers_and_health_rendering(self):
        rss = b'''<rss version="2.0"><channel><title>Example</title><item><title>New</title>
        <link>https://example.com/new</link><pubDate>Tue, 17 Nov 2026 11:00:00 GMT</pubDate></item></channel></rss>'''

        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, content=rss)
            return httpx.Response(200, json={"results": [row()]})

        config = {"settings": SETTINGS, "categories": {"商业变现与独立开发": [{"name": "Example", "url": "https://example.com/feed"}]}}
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}), patch.object(main.httpx, "AsyncClient", return_value=client):
            articles, health = await main.collect_articles(config, NOW)
        self.assertEqual([a.id for a in articles], ["S001", "S002"])
        text = main.render_markdown(None, articles, health, NOW, 24)
        visible = BeautifulSoup(main.render_html(text), "html.parser").get_text(" ", strip=True)
        self.assertIn("X 独立开发大佬池 (Tavily)：success (1条)", visible)
        self.assertIn("发布时间未核实", visible)
        self.assertIn(main.X_LIST_URL, main.render_html(text))

    def test_action_requires_both_tracks_and_brand(self):
        articles, brief = main.demo_data(NOW)
        for action in ["single plan", {"overseas": "X"}, {"domestic": "小红书"}, {"overseas": "X", "domestic": ""}]:
            invalid = copy.deepcopy(brief)
            invalid["action"] = action
            with self.assertRaises(main.BriefError):
                main.validate_brief(invalid, articles)
        main.validate_brief(brief, articles)
        text = main.render_markdown(brief, articles, [], NOW, 24)
        self.assertIn("Prism Scout", text)
        self.assertIn("海外平台", text)
        self.assertIn("国内平台", text)


if __name__ == "__main__":
    unittest.main()
