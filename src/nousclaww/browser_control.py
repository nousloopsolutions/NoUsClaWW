"""BrowserControl — Playwright wrapper gated by ControlState.

Provides a minimal browser automation tool that the agent can use to
navigate web pages, extract text, and click elements. All operations
are gated behind ``JS_EVAL_ENABLED`` (for JavaScript execution) and
``OBSERVE_ENABLED`` (for page navigation/reading).

Playwright is an optional dependency. If it is not installed, the
constructor raises ``ImportError`` with a helpful message, but the
module itself imports cleanly (AGENTS.md Section 4.4).

SYNTH:
    purpose: Playwright-based browser automation tool gated by control flags.
    axioms: [local_first, epistemic_boundary, reversibility_awareness, honest_failure_over_fake_success]
    objective: Allow the agent to browse web pages and extract information
        when the user has explicitly enabled browser access, with honest
        error reporting and no silent JavaScript execution.
    anti_patterns:
        - Never execute JavaScript without JS_EVAL_ENABLED
        - Never navigate to a page without OBSERVE_ENABLED
        - Never fabricate page content
        - Never leave a browser context open after the operation

#C Adapted from the tool registry pattern and nousclaww.control_state
"""

from __future__ import annotations

import logging
from typing import Any

from nousclaww.control_state import ControlFlag, ControlState, ControlStateError

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright, Browser as PlaywrightBrowser
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    PlaywrightBrowser = None  # type: ignore[assignment, misc]


class BrowserControl:
    """Playwright-based browser automation with control-flag gating.

    Args:
        control_state: The ControlState that gates browser operations.
        headless: If True (default), run browser in headless mode.

    Raises:
        ImportError: If Playwright is not installed.
    """

    def __init__(self, control_state: ControlState, headless: bool = True) -> None:
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is required for BrowserControl but is not installed. "
                "Install it with: pip install 'nousclaww[browser]' "
                "or pip install playwright && playwright install"
            )
        self._control_state = control_state
        self._headless = headless
        self._pw = None
        self._browser: PlaywrightBrowser | None = None

    def _ensure_browser(self) -> PlaywrightBrowser:
        """Lazily start the Playwright browser."""
        if self._browser is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self._headless)
        return self._browser

    def _close(self) -> None:
        """Close the browser and Playwright."""
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    def navigate(self, url: str) -> str:
        """Navigate to a URL and return the page text.

        Gated by OBSERVE_ENABLED.
        """
        self._control_state.require(ControlFlag.OBSERVE_ENABLED)
        try:
            browser = self._ensure_browser()
            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                return page.inner_text("body")
            finally:
                page.close()
        except ControlStateError:
            raise
        except Exception as exc:
            logger.error("BrowserControl.navigate failed: %s", exc)
            return f"ERROR: {exc}"

    def click(self, url: str, selector: str) -> str:
        """Navigate to a URL, click an element, and return the resulting page text.

        Gated by OBSERVE_ENABLED.
        """
        self._control_state.require(ControlFlag.OBSERVE_ENABLED)
        try:
            browser = self._ensure_browser()
            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.click(selector, timeout=10000)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                return page.inner_text("body")
            finally:
                page.close()
        except ControlStateError:
            raise
        except Exception as exc:
            logger.error("BrowserControl.click failed: %s", exc)
            return f"ERROR: {exc}"

    def evaluate(self, url: str, script: str) -> str:
        """Navigate to a URL and evaluate a JavaScript expression.

        Gated by JS_EVAL_ENABLED (in addition to OBSERVE_ENABLED).
        """
        self._control_state.require(ControlFlag.OBSERVE_ENABLED)
        self._control_state.require(ControlFlag.JS_EVAL_ENABLED)
        try:
            browser = self._ensure_browser()
            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                result = page.evaluate(script)
                return str(result) if result is not None else "(null)"
            finally:
                page.close()
        except ControlStateError:
            raise
        except Exception as exc:
            logger.error("BrowserControl.evaluate failed: %s", exc)
            return f"ERROR: {exc}"

    def close(self) -> None:
        """Close the browser if it is open."""
        self._close()

    def __del__(self) -> None:
        try:
            self._close()
        except Exception:
            pass
