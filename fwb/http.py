"""合规 HTTP 客户端：robots.txt 校验 + 按主机限速 + 重试 + 条件请求。

所有数据源都必须经由 PoliteClient 发请求，这样合规逻辑只写一处：
- 每个主机首次访问前读取 robots.txt，按 RFC 9309 解释：
  2xx 解析规则；4xx（含 404）视为无限制；5xx/网络错误视为全部禁止（保守）。
- 同一主机两次请求之间至少间隔 min_interval_s 秒。
- 429/5xx 指数退避重试；尊重 Retry-After。
- 支持 ETag / Last-Modified 条件请求，数据未变化时服务器返回 304，省流量也减轻对方负担。
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from fwb.settings import load_settings

log = logging.getLogger(__name__)


class RobotsDisallowed(RuntimeError):
    """目标 URL 被 robots.txt 禁止访问。"""


class RetryableHTTPError(RuntimeError):
    pass


# 单个数据源可恢复的抓取失败：调用方应记录警告并继续处理其他数据源
FETCH_ERRORS = (requests.RequestException, RetryableHTTPError, RobotsDisallowed)


@dataclass
class FetchResult:
    url: str
    status: int
    content: bytes
    etag: str | None
    last_modified: str | None

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    def text(self, encoding: str = "utf-8") -> str:
        return self.content.decode(encoding)

    def json(self):
        import json

        return json.loads(self.content)


class PoliteClient:
    def __init__(self, user_agent: str | None = None, timeout_s: float | None = None):
        cfg = load_settings()["http"]
        self.user_agent = user_agent or cfg["user_agent"]
        self.timeout_s = timeout_s or cfg["timeout_s"]
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.user_agent
        # host → (解析器或 None=无限制, 过期时刻)。成功结论缓存 24h（RFC 9309 建议上限）；
        # 5xx/网络错误得出的"全部禁止"只缓存 10 分钟，避免一次瞬时故障让长驻进程永久不可用。
        self._robots: dict[str, tuple[urllib.robotparser.RobotFileParser | None, float]] = {}
        self._last_hit: dict[str, float] = {}
        self._min_interval: dict[str, float] = {}
        self._lock = threading.Lock()

    # ---------- 配置 ----------
    def set_min_interval(self, host: str, seconds: float) -> None:
        self._min_interval[host] = seconds

    # ---------- robots.txt ----------
    def _robots_for(self, scheme: str, host: str) -> urllib.robotparser.RobotFileParser | None:
        """返回解析器；None 表示"无限制"。"""
        key = f"{scheme}://{host}"
        cached = self._robots.get(key)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        rp = urllib.robotparser.RobotFileParser()
        ttl = 24 * 3600
        try:
            r = self.session.get(f"{key}/robots.txt", timeout=self.timeout_s)
            if 200 <= r.status_code < 300:
                # 能解析多少规则就用多少（RFC 9309 §2.2）；返回 HTML 页面时相当于无规则
                rp.parse(r.text.splitlines())
            elif 400 <= r.status_code < 500:
                rp = None  # RFC 9309 §2.3.1.3：不可用（4xx）→ 可以访问
            else:
                rp.disallow_all, ttl = True, 600  # 5xx → 保守地视为全部禁止（短期）
        except requests.RequestException:
            rp.disallow_all, ttl = True, 600
        self._robots[key] = (rp, time.monotonic() + ttl)
        return rp

    def check_robots(self, url: str) -> None:
        parts = urlsplit(url)
        rp = self._robots_for(parts.scheme, parts.netloc)
        if rp is not None and not rp.can_fetch(self.user_agent, url):
            raise RobotsDisallowed(f"robots.txt 禁止访问：{url}")

    # ---------- 限速 ----------
    def _throttle(self, host: str) -> None:
        interval = self._min_interval.get(host, 1.0)
        with self._lock:
            wait = self._last_hit.get(host, 0.0) + interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_hit[host] = time.monotonic()

    # ---------- 请求 ----------
    @retry(
        retry=retry_if_exception_type((RetryableHTTPError, requests.ConnectionError, requests.Timeout)),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        self.check_robots(url)
        host = urlsplit(url).netloc
        self._throttle(host)
        h = dict(headers or {})
        if etag:
            h["If-None-Match"] = etag
        if last_modified:
            h["If-Modified-Since"] = last_modified
        r = self.session.get(url, params=params, headers=h, timeout=self.timeout_s)
        if r.status_code == 429 or r.status_code >= 500:
            retry_after = r.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                time.sleep(min(int(retry_after), 120))
            raise RetryableHTTPError(f"{r.status_code} {url}")
        if r.status_code != 304:
            r.raise_for_status()
        return FetchResult(
            url=url,
            status=r.status_code,
            content=r.content if r.status_code != 304 else b"",
            etag=r.headers.get("ETag"),
            last_modified=r.headers.get("Last-Modified"),
        )


_client: PoliteClient | None = None


def client() -> PoliteClient:
    """进程内单例，保证同一主机的限速在所有数据源之间共享。"""
    global _client
    if _client is None:
        _client = PoliteClient()
    return _client
