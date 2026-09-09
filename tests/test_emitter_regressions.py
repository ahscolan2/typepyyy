"""Regression tests for Google Docs and Desktop emitter edge cases.

Exercises URL normalization, live text pre-validation, browser sandbox configuration,
immediate and mid-run cancellation, and resource cleanup without requiring
live OS typing or interactive browser logins.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest

emit_common = pytest.importorskip("emit_common")
docs_emitter = pytest.importorskip("docs_emitter")
desktop_emitter = pytest.importorskip("desktop_emitter")


def _make_keystroke(index, char, keydown_ms, keyup_ms, kind="key"):
    return {
        "index": index,
        "kind": kind,
        "char": char,
        "keydown_ms": keydown_ms,
        "keyup_ms": keyup_ms,
        "dwell_ms": keyup_ms - keydown_ms,
        "iki_ms": 0,
        "motor_iki_ms": 0,
        "flight_ms": 0,
        "role": "text",
    }


def _make_record(text, keystrokes):
    return {
        "target_text": text,
        "keystrokes": keystrokes,
    }


# =============================================================================
# URL Normalization Tests
# =============================================================================


@pytest.mark.parametrize(
    "raw_id",
    [
        "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
        "doc_id_with_underscores_and_dashes-123",
        "  1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms  ",
    ],
)
def test_normalize_doc_id_accepts_valid_raw_identifiers(raw_id):
    assert docs_emitter.normalize_doc_id(raw_id) == raw_id.strip()


@pytest.mark.parametrize(
    "url, expected_id",
    [
        (
            "https://docs.google.com/document/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit",
            "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
        ),
        (
            "https://docs.google.com/document/u/0/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit",
            "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
        ),
        (
            "https://docs.google.com/document/u/2/d/doc-id-here",
            "doc-id-here",
        ),
        (
            "https://docs.google.com/document/d/doc-id-here/preview?usp=sharing",
            "doc-id-here",
        ),
        (
            "https://docs.google.com/document/d/doc-id-here#heading=h.123",
            "doc-id-here",
        ),
        (
            "  https://docs.google.com/document/d/doc-id-here/edit  ",
            "doc-id-here",
        ),
        ("HTTPS://DOCS.GOOGLE.COM/document/d/MixedCase-ID/edit", "MixedCase-ID"),
        ("hTtPs://Docs.Google.Com/document/u/1/d/MixedCase-ID", "MixedCase-ID"),
    ],
)
def test_normalize_doc_id_extracts_from_google_docs_urls(url, expected_id):
    assert docs_emitter.normalize_doc_id(url) == expected_id


@pytest.mark.parametrize(
    "bad_url, error_match",
    [
        ("http://docs.google.com/document/d/123/edit", "expected an https"),
        ("https://drive.google.com/file/d/123/view", "expected an https"),
        ("https://example.com/document/d/123/edit", "expected an https"),
        ("https://docs.google.com/spreadsheets/d/123/edit", "expected an https"),
        ("https://docs.google.com/document/d/", "expected an https"),
        ("https://docs.google.com/document/d/bad$id/edit", "expected an https"),
        ("HTTPS://docs.google.com.evil.example/document/d/123/edit", "expected an https"),
        ("HTTPS://user@docs.google.com/document/d/123/edit", "expected an https"),
    ],
)
def test_normalize_doc_id_rejects_invalid_urls(bad_url, error_match):
    with pytest.raises(ValueError, match=error_match):
        docs_emitter.normalize_doc_id(bad_url)


@pytest.mark.parametrize(
    "bad_input",
    [
        "",
        "   ",
        "../path/traversal",
        "has spaces inside",
        "invalid!chars",
        12345,
        None,
        [],
    ],
)
def test_normalize_doc_id_rejects_malformed_non_urls(bad_input):
    with pytest.raises(ValueError, match="doc_id must be a Google Docs URL or document ID"):
        docs_emitter.normalize_doc_id(bad_input)


@pytest.mark.parametrize("char", ["İ", "K", "é", "１"])
def test_non_ascii_characters_use_literal_text_instead_of_an_invalid_physical_code(char):
    descriptor = docs_emitter.descriptor_for(char)
    assert descriptor["code"] == ""
    assert descriptor["windowsVirtualKeyCode"] == 0
    assert descriptor["text"] == char


# =============================================================================
# Stream Validation Tests (validate_live_text)
# =============================================================================


def test_validate_live_text_accepts_clean_stream():
    record = _make_record(
        "Hello\tworld\n",
        [
            _make_keystroke(0, "H", 0, 50),
            _make_keystroke(1, "e", 60, 110),
            _make_keystroke(2, "\t", 120, 170),
            _make_keystroke(3, "\n", 180, 230),
        ],
    )
    # Should complete without error
    emit_common.validate_live_text(record)


@pytest.mark.parametrize(
    "control_char",
    ["\x00", "\x1b", "\r", "\x07", "\x08"],
)
def test_validate_live_text_rejects_control_characters(control_char):
    record = _make_record(
        "x",
        [_make_keystroke(0, control_char, 0, 50)],
    )
    with pytest.raises(ValueError, match="unsupported live-editor control character.*use LF newlines"):
        emit_common.validate_live_text(record)


def test_validate_live_text_rejects_backspace_on_empty_buffer():
    record = _make_record(
        "",
        [_make_keystroke(0, None, 0, 50, kind="backspace")],
    )
    with pytest.raises(ValueError, match="replay would backspace before the start of its text"):
        emit_common.validate_live_text(record)


@pytest.mark.parametrize(
    "cluster_char, description",
    [
        ("\u0301", "combining acute accent"),
        ("\u0308", "combining diaeresis"),
        ("\u200d", "zero-width joiner"),
        ("\U0001F3FB", "emoji skin-tone modifier"),
        ("\U0001F1FA", "regional indicator symbol"),
        ("\u1100", "Hangul choseong jamo"),
        ("\U000E0020", "tag character"),
    ],
)
def test_validate_live_text_rejects_deletions_inside_unicode_clusters(cluster_char, description):
    record = _make_record(
        "e",
        [
            _make_keystroke(0, "e", 0, 50),
            _make_keystroke(1, cluster_char, 60, 110),
            _make_keystroke(2, None, 120, 170, kind="backspace"),
        ],
    )
    with pytest.raises(ValueError, match="replay deletes within a Unicode grapheme"):
        emit_common.validate_live_text(record)


def test_validate_live_text_rejects_deleting_char_preceded_by_zwj():
    record = _make_record(
        "",
        [
            _make_keystroke(0, "a", 0, 50),
            _make_keystroke(1, "\u200d", 60, 110),
            _make_keystroke(2, "b", 120, 170),
            _make_keystroke(3, None, 180, 230, kind="backspace"),
        ],
    )
    with pytest.raises(ValueError, match="replay deletes within a Unicode grapheme"):
        emit_common.validate_live_text(record)


def test_docs_emitter_validates_stream_before_browser_launch(monkeypatch):
    record = _make_record(
        "",
        [_make_keystroke(0, "\x00", 0, 50)],
    )

    def fail_playwright(*args, **kwargs):
        pytest.fail("Playwright should not be imported or invoked for invalid streams")

    monkeypatch.setitem(sys.modules, "playwright", MagicMock(side_effect=fail_playwright))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", MagicMock(side_effect=fail_playwright))

    with pytest.raises(ValueError, match="unsupported live-editor control character"):
        docs_emitter.emit_to_google_docs(record, doc_id="valid-doc-id")


def test_desktop_emitter_validates_stream_before_countdown(monkeypatch):
    record = _make_record(
        "",
        [_make_keystroke(0, "\x1b", 0, 50)],
    )

    def fail_pynput(*args, **kwargs):
        pytest.fail("pynput should not be invoked for invalid streams")

    monkeypatch.setitem(sys.modules, "pynput", MagicMock(side_effect=fail_pynput))

    with pytest.raises(ValueError, match="unsupported live-editor control character"):
        desktop_emitter.emit_to_desktop(record, initial_delay_s=5.0)


# =============================================================================
# Browser Sandbox and Launch Options Tests
# =============================================================================


def test_browser_channel_validation():
    record = _make_record("a", [_make_keystroke(0, "a", 0, 50)])
    with pytest.raises(ValueError, match="browser_channel must be chromium, chrome or msedge"):
        docs_emitter.emit_to_google_docs(record, doc_id="abc", browser_channel="firefox")


def test_editor_timeout_validation():
    record = _make_record("a", [_make_keystroke(0, "a", 0, 50)])
    with pytest.raises(ValueError, match="editor_timeout_s must be finite and greater than 0"):
        docs_emitter.emit_to_google_docs(record, doc_id="abc", editor_timeout_s=0)


def test_docs_emitter_launches_with_explicit_sandbox(monkeypatch, tmp_path):
    launch_kwargs = {}

    class FakeChromium:
        def launch_persistent_context(self, user_data_dir, **kwargs):
            launch_kwargs.update(kwargs)
            context = MagicMock()
            page = MagicMock()
            page.is_closed.return_value = False
            context.pages = [page]
            context.new_cdp_session.return_value = MagicMock()
            return context

    class FakePlaywrightContextManager:
        def __enter__(self):
            pw = MagicMock()
            pw.chromium = FakeChromium()
            return pw

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

    playwright_mod = types.ModuleType("playwright")
    sync_api_mod = types.ModuleType("playwright.sync_api")
    sync_api_mod.sync_playwright = FakePlaywrightContextManager
    sync_api_mod.Error = Exception
    sync_api_mod.TimeoutError = TimeoutError

    monkeypatch.setitem(sys.modules, "playwright", playwright_mod)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api_mod)

    record = _make_record("a", [_make_keystroke(0, "a", 0, 50)])

    def abort_trigger():
        if launch_kwargs:
            return True
        return False

    docs_emitter.emit_to_google_docs(
        record,
        doc_id="test-doc-123",
        browser_channel="chrome",
        headless=True,
        profile_dir=str(tmp_path / "profile"),
        should_abort=abort_trigger,
    )

    assert launch_kwargs.get("chromium_sandbox") is True
    assert launch_kwargs.get("channel") == "chrome"
    assert launch_kwargs.get("headless") is True


# =============================================================================
# Emitter Cancellation and Cleanup Tests
# =============================================================================


def test_docs_emitter_pre_cancelled_aborts_without_launch(monkeypatch):
    def fail_playwright(*args, **kwargs):
        pytest.fail("Should not launch browser when pre-cancelled")

    playwright_mod = types.ModuleType("playwright")
    sync_api_mod = types.ModuleType("playwright.sync_api")
    sync_api_mod.sync_playwright = fail_playwright
    sync_api_mod.Error = Exception
    sync_api_mod.TimeoutError = TimeoutError
    monkeypatch.setitem(sys.modules, "playwright", playwright_mod)
    # A pre-cancelled run must work even when the optional browser package
    # is missing, as well as avoid launching an installed browser.
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    record = _make_record("a", [_make_keystroke(0, "a", 0, 50)])
    result = docs_emitter.emit_to_google_docs(
        record, doc_id="test-doc", should_abort=lambda: True
    )

    assert result["aborted"] is True
    assert result["dispatched"] == 0


def test_desktop_emitter_pre_cancelled_aborts_immediately(monkeypatch):
    def fail_pynput(*args, **kwargs):
        pytest.fail("Should not import or run pynput when pre-cancelled")

    monkeypatch.setitem(sys.modules, "pynput", MagicMock(side_effect=fail_pynput))

    record = _make_record("a", [_make_keystroke(0, "a", 0, 50)])
    result = desktop_emitter.emit_to_desktop(
        record, initial_delay_s=5.0, should_abort=lambda: True
    )

    assert result["aborted"] is True
    assert result["dispatched"] == 0
    assert result["duration_s"] == 0.0


def test_replay_in_page_releases_held_keys_and_detaches_cdp_on_abort():
    cdp_calls = []
    detached = []
    abort_flag = False

    class FakeCDPSession:
        def send(self, method, payload):
            nonlocal abort_flag
            cdp_calls.append((method, payload))
            if payload.get("type") == "char":
                abort_flag = True

        def detach(self):
            detached.append(True)

    fake_cdp = FakeCDPSession()
    page = MagicMock()
    page.is_closed.return_value = False
    page.context.new_cdp_session.return_value = fake_cdp

    record = _make_record(
        "ab",
        [
            _make_keystroke(0, "a", 0, 100),
            _make_keystroke(1, "b", 200, 300),
        ],
    )

    def check_abort():
        return abort_flag

    clock = docs_emitter.replay_in_page(page, record, should_abort=check_abort)

    assert clock["aborted"] is True
    assert clock["dispatched"] == 1
    key_ups = [p for m, p in cdp_calls if p.get("type") == "keyUp" and p.get("key") == "a"]
    assert len(key_ups) >= 1
    assert detached == [True]


def test_replay_in_page_aborts_when_page_is_closed():
    fake_cdp = MagicMock()
    page = MagicMock()
    page.is_closed.return_value = True
    page.context.new_cdp_session.return_value = fake_cdp

    record = _make_record("a", [_make_keystroke(0, "a", 0, 50)])
    clock = docs_emitter.replay_in_page(page, record)

    assert clock["aborted"] is True
    assert clock["dispatched"] == 0
    page.context.new_cdp_session.assert_not_called()


def test_replay_in_page_preserves_progress_when_browser_closes_during_dispatch():
    page = MagicMock()
    page.is_closed.return_value = False
    cdp = page.context.new_cdp_session.return_value

    def send(_method, payload):
        if payload["type"] == "rawKeyDown" and payload["key"] == "b":
            page.is_closed.return_value = True
            raise RuntimeError("Target closed")

    cdp.send.side_effect = send
    record = _make_record("ab", [
        _make_keystroke(0, "a", 0, 0), _make_keystroke(1, "b", 1, 1),
    ])
    clock = docs_emitter.replay_in_page(page, record)
    assert clock["aborted"] is True
    assert clock["dispatched"] == 2
    cdp.detach.assert_called_once()


def test_replay_in_page_releases_shift_after_a_failed_character_release():
    page = MagicMock()
    page.is_closed.return_value = False
    cdp = page.context.new_cdp_session.return_value
    calls = []

    def send(_method, payload):
        calls.append((payload["type"], payload["key"]))
        if payload["type"] == "keyUp" and payload["key"] == "A":
            raise RuntimeError("Device failure")

    cdp.send.side_effect = send
    record = _make_record("A", [_make_keystroke(0, "A", 0, 0)])
    with pytest.raises(RuntimeError, match="Device failure"):
        docs_emitter.replay_in_page(page, record)
    assert calls[-1] == ("keyUp", "Shift")
    cdp.detach.assert_called_once()


class _BrowserError(Exception):
    pass


class _BrowserTimeout(_BrowserError):
    pass


def _install_browser(monkeypatch, page):
    context = MagicMock()
    context.pages = [page]
    context.close.side_effect = lambda: setattr(page.is_closed, "return_value", True)
    manager = MagicMock()
    manager.__enter__.return_value.chromium.launch_persistent_context.return_value = context
    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: manager
    module.Error = _BrowserError
    module.TimeoutError = _BrowserTimeout
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return context


def test_docs_waits_for_an_editable_focusable_target_before_moving_the_cursor(monkeypatch):
    page = MagicMock()
    page.is_closed.return_value = False
    editor = page.frame_locator.return_value.locator.return_value
    editor.is_editable.side_effect = [False, True, True]
    page.locator.return_value.click.side_effect = [_BrowserTimeout("Not ready"), None]
    context = _install_browser(monkeypatch, page)
    monkeypatch.setattr(docs_emitter, "replay_in_page", lambda *_args, **_kwargs: {
        "dispatched": 0, "aborted": True, "duration_s": 0.0,
    })
    record = _make_record("a", [_make_keystroke(0, "a", 0, 0)])
    result = docs_emitter.emit_to_google_docs(record, doc_id="test")
    assert result["aborted"] is True
    assert editor.is_editable.call_count == 3
    editor.focus.assert_called_once()
    page.keyboard.press.assert_called_once()
    context.close.assert_called_once()


def test_docs_stop_during_readiness_does_not_move_cursor_or_replay(monkeypatch):
    page = MagicMock()
    page.is_closed.return_value = False
    context = _install_browser(monkeypatch, page)
    cancelled = False

    def wait(*_args, **_kwargs):
        nonlocal cancelled
        cancelled = True

    page.wait_for_selector.side_effect = wait
    record = _make_record("a", [_make_keystroke(0, "a", 0, 0)])
    result = docs_emitter.emit_to_google_docs(record, doc_id="test", should_abort=lambda: cancelled)
    assert result["aborted"] is True
    page.keyboard.press.assert_not_called()
    page.context.new_cdp_session.assert_not_called()
    context.close.assert_called_once()


def test_docs_reports_dispatch_errors_even_after_its_own_cleanup_closes_the_page(monkeypatch):
    page = MagicMock()
    page.is_closed.return_value = False
    context = _install_browser(monkeypatch, page)
    page.keyboard.press.side_effect = _BrowserError("Permission denied")
    record = _make_record("a", [_make_keystroke(0, "a", 0, 0)])
    with pytest.raises(RuntimeError, match="Permission denied"):
        docs_emitter.emit_to_google_docs(record, doc_id="test")
    context.close.assert_called_once()


def test_docs_browser_closure_during_readiness_is_an_abort(monkeypatch):
    page = MagicMock()
    page.is_closed.return_value = False
    context = _install_browser(monkeypatch, page)

    def closed(*_args, **_kwargs):
        page.is_closed.return_value = True
        raise _BrowserError("Target closed")

    page.wait_for_selector.side_effect = closed
    record = _make_record("a", [_make_keystroke(0, "a", 0, 0)])
    result = docs_emitter.emit_to_google_docs(record, doc_id="test")
    assert result["aborted"] is True
    page.keyboard.press.assert_not_called()
    context.close.assert_called_once()
