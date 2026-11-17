"""Offline tests: no live feeds, paid API requests, or actual emails."""

import argparse
import asyncio
import copy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from bs4 import BeautifulSoup
import httpx

import main


NOW = datetime(2026, 5, 6, 0, 0, tzinfo=timezone.utc)
SETTINGS = main.load_config(main.ROOT / "feeds.yaml")["settings"]


def rss_item(title, hours=1, url=None, date=True):
    timestamp = f"<pubDate>{format_datetime(NOW - timedelta(hours=hours))}</pubDate>" if date else ""
    return (f"<item><title>{title}</title><link>{url or 'https://example.com/' + title}</link>"
            f"{timestamp}<description>&lt;p&gt;Useful update&lt;/p&gt;</description></item>")


def rss(*items):
    return ('<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title>'
            '<link>https://example.com</link><description>Test</description>'
            + ''.join(items) + '</channel></rss>').encode()


class FeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_window_override_and_report(self):
        data = rss(rss_item("new", 1), rss_item("older", 48), rss_item("boundary", 72), rss_item("expired", 72.01))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=data))) as client:
            normal, _ = await main.fetch_one(client, "test", {"name": "Daily", "url": "https://example.com/feed"}, NOW, SETTINGS)
            slow, health = await main.fetch_one(client, "test", {"name": "MicroConf", "url": "https://example.com/feed", "lookback_hours": 72}, NOW, SETTINGS)
        self.assertEqual([a.title for a in normal], ["new"])
        self.assertEqual([a.title for a in slow], ["new", "older", "boundary"])
        self.assertEqual(SETTINGS["lookback_hours"], 24)
        self.assertEqual(health["lookback_hours"], 72)
        for i, article in enumerate(slow):
            article.id = f"S{i:03d}"
        text = main.render_markdown(None, slow, [health], NOW, 24)
        self.assertIn("指定低频源 72 小时", text)
        self.assertIn("近三日更新", text)

    async def test_reddit_429_retries_once_after_three_seconds(self):
        for persistent in (False, True):
            calls = []

            def handler(request):
                calls.append(request)
                self.assertIn("Mozilla/5.0", request.headers["User-Agent"])
                if persistent or len(calls) == 1:
                    return httpx.Response(429)
                return httpx.Response(200, content=rss(rss_item("recovered")))

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch.object(main.asyncio, "sleep", new_callable=AsyncMock) as sleep:
                    articles, health = await main.fetch_one(client, "test", {"name": "Reddit", "url": "https://www.reddit.com/r/SaaS/top/.rss?t=day", "retry_429_delay_seconds": 3}, NOW, SETTINGS)
            sleep.assert_awaited_once_with(3)
            self.assertEqual(len(calls), 2)
            self.assertEqual(health["status"], "failed" if persistent else "ok")
            self.assertEqual(len(articles), 0 if persistent else 1)

    def test_feed_config_windows_and_removed_sources(self):
        config = main.load_config(main.ROOT / "feeds.yaml")
        specs = [spec for feeds in config["categories"].values() for spec in feeds]
        self.assertEqual({s["name"] for s in specs if s.get("lookback_hours") == 72}, {"MicroConf", "Hugging Face Blog", "Transformers Releases"})
        urls = [s["url"] for s in specs]
        self.assertFalse(any("ihrss.io" in u or "/SideProject/" in u or "/LocalLLaMA/" in u for u in urls))
        self.assertTrue(any("/SaaS/" in u for u in urls))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feeds.yaml"
            path.write_text("categories:\n  test:\n    - name: invalid\n      lookback_hours: 73\n", encoding="utf-8")
            with self.assertRaises(main.BriefError):
                main.load_config(path)

    def test_dates_boundaries_and_missing_dates(self):
        articles = main.parse_articles(rss(
            rss_item("new"), rss_item("boundary", 24), rss_item("old", 24.01),
            rss_item("future", -0.01), rss_item("unknown", date=False)), "test", "source", NOW, SETTINGS)
        self.assertEqual([a.title for a in articles], ["new", "boundary"])

    def test_36_hour_window_and_tracking_deduplication(self):
        settings = {**SETTINGS, "lookback_hours": 36}
        articles = main.parse_articles(rss(rss_item("one", 30, "https://example.com/a?utm_source=rss"),
                                          rss_item("two", 31, "https://example.com/a#part"),
                                          rss_item("too-old", 37)), "test", "source", NOW, settings)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "one")

    def test_atom_updated_does_not_refresh_old_publication(self):
        data = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title>
          <entry><id>1</id><title>Old</title><link href="https://example.com/old"/>
            <published>2020-01-01T00:00:00Z</published><updated>2026-05-05T20:00:00Z</updated></entry>
          <entry><id>2</id><title>Updated only</title><link href="https://example.com/new"/>
            <updated>2026-05-05T20:00:00Z</updated></entry>
          <entry><id>3</id><title>Invalid</title><link href="https://example.com/invalid"/>
            <published>invalid</published><updated>2026-05-05T20:00:00Z</updated></entry></feed>'''
        articles = main.parse_articles(data, "test", "source", NOW, SETTINGS)
        self.assertEqual([a.title for a in articles], ["Updated only"])

    async def test_timeout_http_errors_and_invalid_xml_are_isolated(self):
        def handler(request):
            if request.url.path == "/timeout":
                raise httpx.ReadTimeout("secret must not be logged", request=request)
            if request.url.path == "/error":
                return httpx.Response(503)
            if request.url.path == "/html":
                return httpx.Response(200, text="<html>Login required</html>")
            return httpx.Response(200, content=rss(rss_item("good")))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await asyncio.gather(*(main.fetch_one(client, "test",
                {"name": path, "url": "https://example.com/" + path}, NOW, SETTINGS)
                for path in ["timeout", "error", "html", "good"]))
        self.assertEqual([r[1]["status"] for r in results], ["failed", "failed", "failed", "ok"])
        self.assertEqual(results[-1][0][0].title, "good")

    async def test_whole_download_deadline(self):
        async def handler(request):
            await asyncio.sleep(0.1)
            return httpx.Response(200, content=rss(rss_item("late")))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.object(main, "FEED_TIMEOUT", 0.01):
                articles, health = await main.fetch_one(client, "test", {"name": "slow", "url": "https://example.com"}, NOW, SETTINGS)
        self.assertEqual(articles, [])
        self.assertEqual(health["reason"], "TimeoutError")

    async def test_missing_feed_variable_skips_without_request(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("Unexpected network"))) as client:
            with patch.dict(os.environ, {}, clear=True):
                _, health = await main.fetch_one(client, "test", {"name": "custom", "url": "${CUSTOM_FEED_BASE}/feed"}, NOW, SETTINGS)
        self.assertEqual(health["status"], "skipped")

    async def test_response_size_limit(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 101))) as client:
            with patch.object(main, "MAX_FEED_BYTES", 100):
                _, health = await main.fetch_one(client, "test", {"url": "https://example.com"}, NOW, SETTINGS)
        self.assertEqual(health["status"], "failed")

    async def test_collection_category_quotas_and_global_deduplication(self):
        config = {"settings": {**SETTINGS, "max_items_per_category": 1}, "categories": {
            "business": [{"name": "one", "url": "https://example.com/one"}],
            "engineering": [{"name": "two", "url": "https://example.com/two"}]}}

        def handler(request):
            if request.url.path == "/one":
                return httpx.Response(200, content=rss(rss_item("shared"), rss_item("older", 2)))
            return httpx.Response(200, content=rss(rss_item("shared"), rss_item("unique", 2)))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}), patch.object(main.httpx, "AsyncClient", return_value=client):
            articles, health = await main.collect_articles(config, NOW)
        self.assertEqual([a.title for a in articles], ["shared", "unique"])
        self.assertEqual([a.id for a in articles], ["S001", "S002"])
        self.assertTrue(all(h["status"] == "ok" for h in health[:-3]))
        self.assertTrue(all(h["reason"] == "missing TAVILY_API_KEY" for h in health[-3:]))

    def test_excessive_lookback_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feeds.yaml"
            path.write_text("settings:\n  lookback_hours: 37\ncategories: {}", encoding="utf-8")
            with self.assertRaises(main.BriefError):
                main.load_config(path)


class ModelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.articles, self.brief = main.demo_data(NOW)
        self.env = {"LLM_API_KEY": "fake-test-key", "LLM_BASE_URL": "https://api.example.com/v1", "LLM_MODEL": "test-model"}

    def test_unknown_source_and_invalid_rating_rejected(self):
        for value in [0, 6, True, "4"]:
            bad = copy.deepcopy(self.brief)
            bad["ideas"][0]["feasibility"] = value
            with self.assertRaises(main.BriefError):
                main.validate_brief(bad, self.articles)
        bad = copy.deepcopy(self.brief)
        bad["ideas"][0]["source_ids"] = ["S999"]
        with self.assertRaises(main.BriefError):
            main.validate_brief(bad, self.articles)

    async def test_compatible_request_and_transient_retry(self):
        calls = []

        def handler(request):
            calls.append(request)
            self.assertEqual(str(request.url), "https://api.example.com/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer fake-test-key")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "test-model")
            self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
            if len(calls) == 1:
                return httpx.Response(429)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": "```json\n" + json.dumps(self.brief) + "\n```"}}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch.dict(os.environ, self.env), patch.object(main.httpx, "AsyncClient", return_value=client), patch.object(main.asyncio, "sleep", new_callable=AsyncMock):
            result = await main.generate_brief(self.articles, NOW)
        self.assertEqual(result, self.brief)
        self.assertEqual(len(calls), 2)

    async def test_auth_failure_not_retried_or_leaked(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(401, text="private provider error")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch.dict(os.environ, self.env), patch.object(main.httpx, "AsyncClient", return_value=client):
            with self.assertRaises(main.BriefError) as caught:
                await main.generate_brief(self.articles, NOW)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("private", str(caught.exception))
        self.assertIn("401", str(caught.exception))

    async def test_malformed_or_truncated_response_rejected(self):
        for response in [{"choices": []}, {"choices": [{"finish_reason": "length"}]},
                         {"choices": [{"message": {"content": "not json"}}]}]:
            client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)))
            with patch.dict(os.environ, self.env), patch.object(main.httpx, "AsyncClient", return_value=client):
                with self.assertRaises(main.BriefError):
                    await main.generate_brief(self.articles, NOW)


class RenderingAndDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def test_four_sections_and_mobile_html_sanitization(self):
        articles, brief = main.demo_data(NOW)
        brief["ideas"][0]["title"] = '<img src=x onerror=alert(1)> [attack](javascript:alert(1))'
        text = main.render_markdown(brief, articles, [], NOW, 24, demo=True)
        for heading in main.SECTIONS:
            self.assertIn("## " + heading, text)
        soup = BeautifulSoup(main.render_html(text), "html.parser")
        self.assertIsNone(soup.find("img"))
        self.assertIsNone(soup.find("script"))
        self.assertTrue(soup.find("meta", attrs={"name": "viewport"}))
        self.assertTrue(all(a.get("href", "").startswith("https://example.com") or a.get("href") == main.X_LIST_URL for a in soup.find_all("a")))
        self.assertIsNotNone(soup.find("a", href=main.X_LIST_URL))
        self.assertGreaterEqual(len(soup.find_all("h2")), 4)

    def test_smtp_tls_modes_and_multipart_message(self):
        config = {"host": "smtp.example.com", "port": 465, "user": "user@example.com",
                  "password": "fake", "sender": "user@example.com", "recipients": ["to@example.com"]}
        # UTC 日期尚为 5 日，北京已经是 6 日。
        date = NOW - timedelta(hours=1)
        for port, factory in [(465, "SMTP_SSL"), (587, "SMTP")]:
            with patch.object(main.smtplib, factory) as mock:
                server = mock.return_value
                server.__enter__.return_value = server
                server.send_message.return_value = {}
                main.send_email("plain body", "<p>HTML body</p>", date, {**config, "port": port})
                message = server.send_message.call_args.args[0]
                self.assertIn("2026-05-06", message["Subject"])
                self.assertEqual(message.get_content_type(), "multipart/alternative")
                self.assertEqual(message.get_body(preferencelist=("html",)).get_content_type(), "text/html")
                self.assertEqual(server.starttls.call_count, 0 if port == 465 else 1)
                server.login.assert_called_once_with("user@example.com", "fake")

    async def test_no_articles_skips_model_and_saves_honest_report(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(feeds=main.ROOT / "feeds.yaml", output_dir=Path(directory), dry_run=True, demo=False)
            with patch.object(main, "collect_articles", new_callable=AsyncMock, return_value=([], [])), patch.object(main, "generate_brief", new_callable=AsyncMock) as model, patch.object(main, "send_email") as send:
                result = await main.run(args)
            self.assertEqual(result, 0)
            model.assert_not_called()
            send.assert_not_called()
            text = next(Path(directory).glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("未取得时间窗口内的有效资讯", text)

    async def test_model_failure_sends_source_fallback_and_signals_failure(self):
        articles, _ = main.demo_data(NOW)
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(feeds=main.ROOT / "feeds.yaml", output_dir=Path(directory), dry_run=False, demo=False)
            with patch.dict(os.environ, {"DRY_RUN": "false"}), patch.object(main, "smtp_settings", return_value={}), patch.object(main, "collect_articles", new_callable=AsyncMock, return_value=(articles, [])), patch.object(main, "generate_brief", new_callable=AsyncMock, side_effect=main.BriefError("测试故障")), patch.object(main, "send_email") as send:
                result = await main.run(args)
            self.assertEqual(result, 1)
            send.assert_called_once()
            self.assertIn("未生成商业分析", send.call_args.args[0])
            self.assertIn("https://example.com/demo", send.call_args.args[0])
            self.assertTrue(list(Path(directory).glob("*.html")))


if __name__ == "__main__":
    unittest.main()
