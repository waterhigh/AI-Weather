"""Tavily integration tests with mock HTTP only; no API credits consumed."""
import copy
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from bs4 import BeautifulSoup
import main

NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
SETTINGS = main.load_config(main.ROOT / "feeds.yaml")["settings"]


def row(url="https://x.com/dotey/status/123", **extra):
    return {"url": url, "title": "OCR workflow", "content": "<p>Visual document processing</p>", **extra}


class TavilyTests(unittest.IsolatedAsyncioTestCase):
    def test_x_hosts_suffixes_and_identity(self):
        keys = set()
        for host in ("x.com", "www.x.com", "twitter.com", "www.twitter.com"):
            for suffix in ("", "/", "/photo/1", "/video/2", "/analytics", "/other/path"):
                url = f"https://{host}/Dotey/status/123{suffix}?s=20"
                item = main.parse_search_result(row(url), NOW, 1400, "test", "followed", "week")
                self.assertEqual(item.url, "https://x.com/dotey/status/123")
                self.assertEqual(item.evidence_type, "x_post")
                keys.add(main.article_key(item))
                # article_key 本身也支持未归一化的别名/后缀。
                item.url = url
                keys.add(main.article_key(item))
        self.assertEqual(keys, {"x:post:123"})
        for url in ("https://twitter.com.evil.example/dotey/status/123", "https://x.com/dotey/status/123abc", "https://x.com/outsider/status/123"):
            self.assertIsNone(main.parse_search_result(row(url), NOW, 1400, "test", "followed", "week"))
        self.assertIsNotNone(main.parse_search_result(row("https://twitter.com/outsider/status/123"), NOW, 1400, "test", "x", "week"))

    def test_only_followed_profiles_are_kept_and_not_dated_as_posts(self):
        for account in main.X_FOLLOWED:
            for host in ("x.com", "www.twitter.com"):
                item = main.parse_search_result(row(f"https://{host}/{account}/", published_date=NOW.isoformat()), NOW, 1400, "test", "followed", "week")
                self.assertEqual(item.url, f"https://x.com/{account}")
                self.assertEqual(item.evidence_type, "x_profile")
                self.assertEqual(item.published_at, "")
                self.assertEqual(main.article_key(item), f"x:profile:{account}")
                self.assertIsNone(main.parse_search_result(row(item.url), NOW, 1400, "test", "x", "week"))
        for url in ("https://x.com/outsider", "https://x.com/dotey/likes", "https://x.com/dotey/status"):
            self.assertIsNone(main.parse_search_result(row(url), NOW, 1400, "test", "followed", "week"))

    def test_week_window_boundaries_and_day_window_stays_strict(self):
        for days in (2, 5, 7):
            data = row(published_date=(NOW - timedelta(days=days)).isoformat())
            self.assertIsNotNone(main.parse_search_result(data, NOW, 1400, "test", "x", "week"))
            community = {**data, "url": "https://reddit.com/r/SaaS/comments/abc/revenue/"}
            self.assertIsNone(main.parse_search_result(community, NOW, 1400, "test", "community", "day"))
        for date in (NOW - timedelta(days=7, seconds=1), NOW + timedelta(seconds=1)):
            self.assertIsNone(main.parse_search_result(row(published_date=date.isoformat()), NOW, 1400, "test", "x", "week"))

    def test_week_and_profile_report_labels(self):
        post = main.parse_search_result(row(published_date=(NOW - timedelta(days=5)).isoformat()), NOW, 1400, "test", "x", "week")
        profile = main.parse_search_result(row("https://twitter.com/levelsio"), NOW, 1400, "test", "followed", "week")
        for index, item in enumerate([post, profile]):
            item.id = f"S{index:03d}"
        health = [{"source": "test", "status": "success", "kind": "search", "lookback_hours": 168},
                  {"source": "blog", "status": "ok", "lookback_hours": 72}]
        text = main.render_markdown(None, [post, profile], health, NOW, 24)
        self.assertIn("X 搜索 7 天 / 社区搜索 24 小时", text)
        self.assertIn("指定低频源 72 小时", text)
        self.assertNotIn("指定低频源 168", text)
        self.assertIn("近一周动态", text)
        self.assertIn("主页搜索摘要", text)
        self.assertNotIn("近三日更新", text)

    def test_three_search_contracts(self):
        expected = [
            ("(site:x.com/levelsio OR site:twitter.com/levelsio OR site:x.com/marc_louvion OR site:twitter.com/marc_louvion OR site:x.com/dannypostmaa OR site:twitter.com/dannypostmaa OR site:x.com/dotey OR site:twitter.com/dotey OR site:x.com/op7418 OR site:twitter.com/op7418)", 12, ["x.com", "twitter.com"], "week"),
            ('("MRR" OR "launched" OR "Micro-SaaS") (AI OR VLM OR OCR OR SaaS)', 8, ["x.com", "twitter.com"], "week"),
            ('(site:reddit.com/r/SaaS OR site:reddit.com/r/SideProject OR site:reddit.com/r/LocalLLaMA OR site:indiehackers.com) (AI OR VLM OR SaaS OR revenue OR MRR)', 12, ["reddit.com", "indiehackers.com"], "day"),
        ]
        self.assertEqual([(q, n, d, t) for _, q, n, d, _, t in main.TAVILY_QUERIES], expected)

    def test_community_links_are_restricted_and_classified(self):
        for url in ["https://reddit.com.evil.example/r/SideProject/comments/abc/",
                    "https://reddit.com/r/unrelated/comments/abc/", "https://indiehackers.com/",
                    "https://x.com/dotey/status/123", "https://reddit.com/r/SideProject/"]:
            self.assertIsNone(main.parse_search_result(row(url), NOW, 1400, "community", "community"))
        a = main.parse_search_result(row("https://old.reddit.com/r/LocalLLaMA/comments/abc/title/comment?utm_source=x"), NOW, 1400, "community", "community")
        self.assertEqual(a.url, "https://www.reddit.com/r/localllama/comments/abc/")
        self.assertEqual(a.category, "AI工程与视觉前沿")
        b = main.parse_search_result(row("https://www.indiehackers.com/post/ocr-123"), NOW, 1400, "community", "community")
        self.assertEqual(b.category, "商业变现与独立开发")
        c = main.parse_search_result(row("https://reddit.com/r/SaaS/comments/def/revenue/"), NOW, 1400, "community", "community")
        self.assertEqual(c.url, "https://www.reddit.com/r/saas/comments/def/")
        self.assertEqual(c.category, "商业变现与独立开发")

    async def test_exact_payloads_dedup_and_unverified_time(self):
        calls = []

        def handler(request):
            self.assertEqual(str(request.url), "https://api.tavily.com/search")
            self.assertEqual(request.method, "POST")
            body = json.loads(request.content)
            calls.append(body)
            self.assertEqual(body["api_key"], "test-key")
            self.assertEqual(body["search_depth"], "basic")
            self.assertEqual(body["time_range"], "day" if "site:reddit.com" in body["query"] else "week")
            self.assertEqual(body["include_domains"], ["reddit.com", "indiehackers.com"] if "site:reddit.com" in body["query"] else ["x.com", "twitter.com"])
            self.assertEqual(request.extensions["timeout"]["read"], 20)
            if "site:reddit.com" in body["query"]:
                return httpx.Response(200, json={"results": [row("https://www.indiehackers.com/post/ocr-workflow-123")]})
            if body["max_results"] == 12:
                return httpx.Response(200, json={"results": [row(), row("https://x.com/levelsio"), row("https://x.com.evil.example/user/status/123"), row("https://x.com/outsider/status/999")]})
            return httpx.Response(200, json={"results": [row("https://twitter.com/dotey/status/123/analytics?utm_source=x"),
                row("https://x.com/op7418/status/456", published_date="2026-11-12T11:00:00Z")]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
                articles, health = await main.fetch_tavily(client, NOW, SETTINGS)
        self.assertEqual({(b["query"], b["max_results"]) for b in calls}, {(q, n) for _, q, n, _, _, _ in main.TAVILY_QUERIES})
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(h["status"] == "success" for h in health))
        self.assertEqual([h["items"] for h in health], [2, 2, 1])
        self.assertEqual([h["lookback_hours"] for h in health], [168, 168, 24])
        self.assertEqual(len(articles), 4)
        self.assertEqual(articles[0].source, main.TAVILY_QUERIES[0][0])
        self.assertEqual(articles[0].published_at, "")
        self.assertEqual(articles[0].summary, "Visual document processing")
        self.assertEqual(articles[1].evidence_type, "x_profile")
        self.assertEqual(articles[2].published_at, "2026-11-12T11:00:00+00:00")

    async def test_partial_failure_keeps_good_results_without_secret_logs(self):
        def handler(request):
            if "site:x.com/levelsio" in json.loads(request.content)["query"]:
                return httpx.Response(429, text="private-key-do-not-log")
            return httpx.Response(200, json={"results": [row()]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.dict(os.environ, {"TAVILY_API_KEY": "private-key-do-not-log"}), self.assertLogs(main.LOG, level="WARNING") as logs:
                articles, health = await main.fetch_tavily(client, NOW, SETTINGS)
        self.assertEqual(len(articles), 1)
        self.assertEqual([h["status"] for h in health], ["failed", "success", "success"])
        self.assertNotIn("private-key-do-not-log", " ".join(logs.output))
        self.assertIn("429", health[0]["reason"])

    async def test_missing_key_makes_no_requests(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("Unexpected network"))) as client:
            with patch.dict(os.environ, {"TAVILY_API_KEY": ""}):
                articles, health = await main.fetch_tavily(client, NOW, SETTINGS)
        self.assertEqual(articles, [])
        self.assertEqual([h["status"] for h in health], ["failed"] * 3)

    async def test_timeout_and_malformed_response_are_isolated(self):
        def handler(request):
            if "site:x.com/levelsio" in json.loads(request.content)["query"]:
                raise httpx.ReadTimeout("timeout", request=request)
            return httpx.Response(200, json={"results": {}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
                articles, health = await main.fetch_tavily(client, NOW, SETTINGS)
        self.assertEqual(articles, [])
        self.assertEqual([h["status"] for h in health], ["failed"] * 3)

    def test_explicit_old_future_and_invalid_dates(self):
        for date in ["2026-11-16T11:59:59Z", "2026-11-17T12:00:01Z"]:
            self.assertIsNone(main.parse_search_result(row(published_date=date), NOW, 1400, "test", "x"))
        for date in ["2026-11-17", "bad-date", None]:
            self.assertEqual(main.parse_search_result(row(published_date=date), NOW, 1400, "test", "x").published_at, "")

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
        self.assertIn("X 关注大佬专属池 (Tavily)：success (1条)", visible)
        self.assertIn("X 商业变现广撒网 (Tavily)：success (1条)", visible)
        self.assertIn("Reddit与IH精选池 (Tavily)：success (0条)", visible)
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
