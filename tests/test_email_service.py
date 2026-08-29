"""Hợp đồng email giao dịch: sender Resend và template OTP của P-118."""

from types import SimpleNamespace

import pytest

from src.services import email_service


def _settings(**overrides):
    values = {
        "app_env": "test",
        "resend_api_key": "re_test_only",
        "resend_from_email": "no-reply@account.c3-app-118.io.vn",
        "resend_from_name": "P-118",
        "resend_reply_to": None,
        "frontend_base_url": "https://www.c3-app-118.io.vn",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_otp_email_is_branded_readable_and_has_a_plain_text_version():
    content = email_service._otp_email("123456")

    assert content.subject == "Mã xác nhận đăng ký P-118"
    assert "123 456" in content.text
    assert "123 456" in content.html
    assert "5 phút" in content.text
    assert "c3-app-118.io.vn" in content.html
    assert "AI20K Agent" not in content.html


def test_otp_template_escapes_untrusted_content():
    content = email_service._otp_email("<script>")

    assert "<script>" not in content.html
    assert "&lt;script&gt;" in content.html


def test_workflow_update_email_uses_the_same_brand_and_plain_text_contract():
    workflow_url = "https://www.c3-app-118.io.vn/workflow/wf-123"
    content = email_service._workflow_update_email("Đơn vị đã xác nhận lịch của bạn.", workflow_url)

    assert content.subject == "P-118 · Cập nhật hành trình dịch vụ"
    assert "Cập nhật hành trình" in content.html
    assert "Đơn vị đã xác nhận lịch của bạn." in content.html
    assert "Đơn vị đã xác nhận lịch của bạn." in content.text
    assert f'href="{workflow_url}"' in content.html
    assert workflow_url in content.text
    assert "Xem chi tiết hành trình" in content.html
    assert "c3-app-118.io.vn" in content.html
    assert "AI20K Agent" not in content.html


@pytest.mark.asyncio
async def test_resend_authentication_is_separate_from_the_visible_sender(monkeypatch):
    captured = {}

    async def fake_post(*, api_key, payload):
        captured["api_key"] = api_key
        captured["payload"] = payload
        return "email-test-id"

    monkeypatch.setattr(email_service, "get_settings", _settings)
    monkeypatch.setattr(email_service, "_post_resend_email", fake_post)

    sent = await email_service._send_email_async(
        "tester@example.com",
        email_service._otp_email("123456"),
    )

    assert sent is True
    assert captured["api_key"] == "re_test_only"
    assert captured["payload"]["from"] == "P-118 <no-reply@account.c3-app-118.io.vn>"
    assert captured["payload"]["to"] == ["tester@example.com"]
    assert captured["payload"]["text"]
    assert captured["payload"]["html"]


@pytest.mark.asyncio
async def test_reply_to_is_only_added_when_configured(monkeypatch):
    captured = {}

    async def fake_post(*, api_key, payload):
        captured["payload"] = payload
        return "email-test-id"

    monkeypatch.setattr(
        email_service,
        "get_settings",
        lambda: _settings(resend_reply_to="support@c3-app-118.io.vn"),
    )
    monkeypatch.setattr(email_service, "_post_resend_email", fake_post)

    await email_service._send_email_async("tester@example.com", email_service._otp_email("123456"))

    assert captured["payload"]["reply_to"] == "support@c3-app-118.io.vn"


@pytest.mark.asyncio
async def test_resend_without_a_verified_from_address_fails_closed(monkeypatch):
    called = False

    async def fake_post(*, api_key, payload):
        nonlocal called
        called = True
        return "email-test-id"

    monkeypatch.setattr(
        email_service,
        "get_settings",
        lambda: _settings(resend_from_email=""),
    )
    monkeypatch.setattr(email_service, "_post_resend_email", fake_post)

    sent = await email_service._send_email_async(
        "tester@example.com",
        email_service._otp_email("123456"),
    )

    assert sent is False
    assert called is False


@pytest.mark.asyncio
async def test_missing_resend_key_fails_closed_outside_development(monkeypatch):
    monkeypatch.setattr(email_service, "get_settings", lambda: _settings(resend_api_key=""))

    sent = await email_service._send_email_async("tester@example.com", email_service._otp_email("123456"))

    assert sent is False


@pytest.mark.asyncio
async def test_workflow_email_escapes_model_text_before_putting_it_in_html(monkeypatch):
    captured = {}

    async def fake_send(_to_email, content):
        captured["content"] = content
        return True

    monkeypatch.setattr(email_service, "get_settings", _settings)
    monkeypatch.setattr(email_service, "_send_email_async", fake_send)

    await email_service.send_workflow_batch_email(
        "tester@example.com",
        "Đã xong <script>alert(1)</script>",
        "wf-123",
    )

    assert "<script>" not in captured["content"].html
    assert "&lt;script&gt;" in captured["content"].html
    assert "Đã xong <script>alert(1)</script>" in captured["content"].text
    assert "https://www.c3-app-118.io.vn/workflow/wf-123" in captured["content"].text


def test_workflow_link_stays_on_the_configured_frontend_and_encodes_the_id():
    url = email_service._workflow_detail_url(
        "https://www.c3-app-118.io.vn/",
        "workflow/with spaces?next=https://evil.example",
    )

    assert url == ("https://www.c3-app-118.io.vn/workflow/workflow%2Fwith%20spaces%3Fnext%3Dhttps%3A%2F%2Fevil.example")


def test_workflow_link_can_select_the_exact_service_task():
    url = email_service._workflow_detail_url(
        "https://www.c3-app-118.io.vn",
        "wf-123",
        "T2/R1",
    )

    assert url == "https://www.c3-app-118.io.vn/workflow/wf-123?task=T2%2FR1"


@pytest.mark.parametrize("base_url", ["", "javascript:alert(1)", "//evil.example"])
def test_workflow_link_rejects_an_invalid_frontend_base_url(base_url):
    with pytest.raises(ValueError, match="FRONTEND_BASE_URL"):
        email_service._workflow_detail_url(base_url, "wf-123")
