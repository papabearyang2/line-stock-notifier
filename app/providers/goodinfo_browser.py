"""One persistent local Chrome page for conservative Goodinfo reads."""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from app.config import get_settings


class GoodinfoBrowser:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._process: subprocess.Popen | None = None

    async def fetch(self, url: str) -> str:
        async with self._lock:
            for attempt in range(2):
                try:
                    return await self._fetch_once(url)
                except ValueError as exc:
                    if str(exc) not in {"browser_error", "browser_start_failed"} or attempt:
                        raise
                    await self._close_unlocked()
                    await asyncio.sleep(1)
        raise ValueError("browser_error")

    async def _fetch_once(self, url: str) -> str:
        page = await self._ensure_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            for _ in range(30):
                html = await page.content()
                text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
                title = await page.title()
                if "Goodinfo!" in title and not self._is_security_page(text):
                    return html
                if "Access Denied" in text or "驗證碼" in text:
                    raise ValueError("access_refused")
                await asyncio.sleep(1)
        except ValueError:
            raise
        except Exception as exc:
            await self._close_unlocked()
            raise ValueError("browser_error") from exc
        raise ValueError("challenge_timeout")

    async def close(self) -> None:
        async with self._lock:
            await self._close_unlocked()

    async def _ensure_page(self):
        if (
            self._browser
            and self._browser.is_connected()
            and self._page
            and not self._page.is_closed()
        ):
            return self._page
        await self._close_unlocked()
        settings = get_settings()
        executable = self._find_executable(settings.goodinfo_browser_executable)
        profile = Path(settings.goodinfo_browser_profile_dir).resolve()
        profile.mkdir(parents=True, exist_ok=True)
        port = self._free_port()
        self._playwright = await async_playwright().start()
        self._process = subprocess.Popen(
            [
                executable,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-position=-32000,-32000",
                "--window-size=1000,800",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(40):
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{port}", timeout=1_000
                )
                context = self._browser.contexts[0]
                self._page = context.pages[0] if context.pages else await context.new_page()
                return self._page
            except Exception:
                await asyncio.sleep(0.25)
        await self._close_unlocked()
        raise ValueError("browser_start_failed")

    async def _close_unlocked(self) -> None:
        page, browser, playwright, process = (
            self._page,
            self._browser,
            self._playwright,
            self._process,
        )
        self._page = self._browser = self._playwright = self._process = None
        if page and not page.is_closed():
            try:
                await page.close()
            except Exception:
                pass
        if browser and browser.is_connected():
            try:
                await browser.close()
            except Exception:
                pass
        if playwright:
            try:
                await playwright.stop()
            except Exception:
                pass
        if process and process.poll() is None:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 5)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                process.kill()

    @staticmethod
    def _find_executable(configured: str) -> str:
        candidates = [
            configured,
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            shutil.which("google-chrome") or "",
            shutil.which("chromium") or "",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return str(candidate)
        raise ValueError("browser_executable_missing")

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as connection:
            connection.bind(("127.0.0.1", 0))
            return int(connection.getsockname()[1])

    @staticmethod
    def _is_security_page(text: str) -> bool:
        return any(
            marker in text
            for marker in (
                "正在執行安全驗證",
                "請稍候",
                "Just a moment",
                "Enable JavaScript and cookies",
            )
        )


goodinfo_browser = GoodinfoBrowser()
