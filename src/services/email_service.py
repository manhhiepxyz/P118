"""Email giao dịch của P-118 qua SMTP (Gmail hoặc Resend)."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from src.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailContent:
    subject: str
    text: str
    html: str


def _otp_email(otp_code: str) -> EmailContent:
    """Dựng email OTP không phụ thuộc SMTP để có thể kiểm thử thuần."""
    code = html.escape(otp_code)
    display_code = f"{code[:3]} {code[3:]}" if len(code) == 6 else code
    subject = "Mã xác nhận đăng ký P-118"
    text = (
        "Xác nhận đăng ký tài khoản P-118\n\n"
        f"Mã xác thực của bạn: {display_code}\n\n"
        "Mã có hiệu lực trong 5 phút. Không chia sẻ mã này với bất kỳ ai.\n"
        "Nếu bạn không thực hiện yêu cầu này, hãy bỏ qua email."
    )
    html_body = f"""<!doctype html>
<html lang="vi">
  <body style="margin:0;background:#f1f6f5;font-family:Inter,Arial,sans-serif;color:#111827">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0">
      Mã xác nhận đăng ký P-118 của bạn có hiệu lực trong 5 phút.
    </div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f1f6f5">
      <tr>
        <td align="center" style="padding:32px 16px">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                 style="max-width:560px;background:#ffffff;border:1px solid #dce8e5;border-radius:20px;overflow:hidden">
            <tr>
              <td style="height:8px;background:#0f9d8f"></td>
            </tr>
            <tr>
              <td style="padding:32px 36px 12px">
                <table role="presentation" cellspacing="0" cellpadding="0">
                  <tr>
                    <td style="width:44px;height:44px;border-radius:12px;background:#0f9d8f;color:#ffffff;
                               text-align:center;font-size:18px;font-weight:700">P</td>
                    <td style="padding-left:12px">
                      <div style="font-size:17px;font-weight:700;color:#111827">P-118</div>
                      <div style="font-size:12px;color:#667085">Trợ lý điều phối dịch vụ cư dân</div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 36px 36px">
                <h1 style="margin:0 0 12px;font-size:24px;line-height:1.3;color:#101828">
                  Xác nhận đăng ký tài khoản
                </h1>
                <p style="margin:0;color:#475467;font-size:15px;line-height:1.65">
                  Nhập mã dưới đây vào P-118 để hoàn tất xác minh email.
                </p>
                <div style="margin:24px 0;padding:20px;border-radius:14px;background:#eaf7f5;border:1px solid #b9e2dc;
                            text-align:center;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;
                            font-size:32px;font-weight:750;letter-spacing:8px;color:#087f73">
                  {display_code}
                </div>
                <p style="margin:0;color:#475467;font-size:14px;line-height:1.65">
                  Mã có hiệu lực trong <strong style="color:#101828">5 phút</strong>.
                  P-118 không bao giờ yêu cầu bạn cung cấp mã này qua điện thoại hoặc tin nhắn.
                </p>
                <div style="margin-top:24px;padding:14px 16px;border-radius:12px;background:#f8fafc;
                            color:#667085;font-size:13px;line-height:1.55">
                  Nếu bạn không thực hiện yêu cầu đăng ký này, bạn có thể bỏ qua email.
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:18px 36px;background:#f8fafc;border-top:1px solid #eaecf0;
                         color:#98a2b3;font-size:12px;line-height:1.5">
                Email tự động từ P-118 · c3-app-118.io.vn
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
    return EmailContent(subject=subject, text=text, html=html_body)


def _reset_password_email(otp_code: str) -> EmailContent:
    """Dựng email quên mật khẩu không phụ thuộc SMTP để có thể kiểm thử thuần."""
    code = html.escape(otp_code)
    display_code = f"{code[:3]} {code[3:]}" if len(code) == 6 else code
    subject = "Mã xác nhận quên mật khẩu P-118"
    text = (
        "Mã xác nhận lấy lại mật khẩu P-118\n\n"
        f"Mã xác thực của bạn: {display_code}\n\n"
        "Mã có hiệu lực trong 5 phút. Không chia sẻ mã này với bất kỳ ai.\n"
        "Nếu bạn không thực hiện yêu cầu này, hãy đổi mật khẩu tài khoản của bạn ngay."
    )
    html_body = f"""<!doctype html>
<html lang="vi">
  <body style="margin:0;background:#f1f6f5;font-family:Inter,Arial,sans-serif;color:#111827">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0">
      Mã xác nhận lấy lại mật khẩu P-118 của bạn có hiệu lực trong 5 phút.
    </div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f1f6f5">
      <tr>
        <td align="center" style="padding:32px 16px">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                 style="max-width:560px;background:#ffffff;border:1px solid #dce8e5;border-radius:20px;overflow:hidden">
            <tr>
              <td style="height:8px;background:#0f9d8f"></td>
            </tr>
            <tr>
              <td style="padding:32px 36px 12px">
                <table role="presentation" cellspacing="0" cellpadding="0">
                  <tr>
                    <td style="width:44px;height:44px;border-radius:12px;background:#0f9d8f;color:#ffffff;
                               text-align:center;font-size:18px;font-weight:700">P</td>
                    <td style="padding-left:12px">
                      <div style="font-size:17px;font-weight:700;color:#111827">P-118</div>
                      <div style="font-size:12px;color:#667085">Trợ lý điều phối dịch vụ cư dân</div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 36px 36px">
                <h1 style="margin:0 0 12px;font-size:24px;line-height:1.3;color:#101828">
                  Yêu cầu lấy lại mật khẩu
                </h1>
                <p style="margin:0;color:#475467;font-size:15px;line-height:1.65">
                  Nhập mã dưới đây vào P-118 để đặt lại mật khẩu mới.
                </p>
                <div style="margin:24px 0;padding:20px;border-radius:14px;background:#eaf7f5;border:1px solid #b9e2dc;
                            text-align:center;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;
                            font-size:32px;font-weight:750;letter-spacing:8px;color:#087f73">
                  {display_code}
                </div>
                <p style="margin:0;color:#475467;font-size:14px;line-height:1.65">
                  Mã có hiệu lực trong <strong style="color:#101828">5 phút</strong>.
                  P-118 không bao giờ yêu cầu bạn cung cấp mã này qua điện thoại hoặc tin nhắn.
                </p>
                <div style="margin-top:24px;padding:14px 16px;border-radius:12px;background:#f8fafc;
                            color:#667085;font-size:13px;line-height:1.55">
                  Nếu bạn không yêu cầu đổi mật khẩu, có thể ai đó đang cố truy cập tài khoản của bạn.
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:18px 36px;background:#f8fafc;border-top:1px solid #eaecf0;
                         color:#98a2b3;font-size:12px;line-height:1.5">
                Email tự động từ P-118 · c3-app-118.io.vn
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
    return EmailContent(subject=subject, text=text, html=html_body)


def _workflow_detail_url(frontend_base_url: str, workflow_id: str, task_id: str | None = None) -> str:
    """Dựng deep-link cùng origin tới đúng workflow; cấu hình sai thì fail-closed."""
    base_url = frontend_base_url.rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("FRONTEND_BASE_URL không hợp lệ")
    url = f"{base_url}/workflow/{quote(workflow_id, safe='')}"
    if task_id:
        url = f"{url}?task={quote(task_id, safe='')}"
    return url


def _workflow_update_email(assistant_message: str, workflow_url: str) -> EmailContent:
    """Dựng email cập nhật hành trình, giữ nội dung model ở dạng văn bản an toàn."""
    safe_message = html.escape(assistant_message).replace("\n", "<br>")
    safe_workflow_url = html.escape(workflow_url, quote=True)
    subject = "P-118 · Cập nhật hành trình dịch vụ"
    text = (
        "P-118 — Cập nhật hành trình dịch vụ\n\n"
        f"{assistant_message}\n\n"
        "Xem chi tiết và thực hiện bước tiếp theo nếu có:\n"
        f"{workflow_url}"
    )
    html_body = f"""<!doctype html>
<html lang="vi">
  <body style="margin:0;background:#f1f6f5;font-family:Inter,Arial,sans-serif;color:#111827">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0">
      Yêu cầu dịch vụ của bạn vừa có cập nhật mới trên P-118.
    </div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f1f6f5">
      <tr>
        <td align="center" style="padding:32px 16px">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                 style="max-width:560px;background:#ffffff;border:1px solid #dce8e5;border-radius:20px;overflow:hidden">
            <tr>
              <td style="height:8px;background:#0f9d8f"></td>
            </tr>
            <tr>
              <td style="padding:32px 36px 12px">
                <table role="presentation" cellspacing="0" cellpadding="0">
                  <tr>
                    <td style="width:44px;height:44px;border-radius:12px;background:#0f9d8f;color:#ffffff;
                               text-align:center;font-size:18px;font-weight:700">P</td>
                    <td style="padding-left:12px">
                      <div style="font-size:17px;font-weight:700;color:#111827">P-118</div>
                      <div style="font-size:12px;color:#667085">Trợ lý điều phối dịch vụ cư dân</div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 36px 36px">
                <div style="display:inline-block;margin-bottom:12px;color:#087f73;font-size:11px;
                            font-weight:700;letter-spacing:1.4px;text-transform:uppercase">
                  Cập nhật hành trình
                </div>
                <h1 style="margin:0 0 12px;font-size:24px;line-height:1.3;color:#101828">
                  Yêu cầu của bạn vừa có tiến triển
                </h1>
                <p style="margin:0;color:#475467;font-size:15px;line-height:1.65">
                  P-118 đã nhận được một cập nhật mới từ hành trình dịch vụ của bạn.
                </p>
                <div style="margin:24px 0;padding:18px 20px;border-radius:14px;background:#f7faf9;
                            border:1px solid #dce8e5;border-left:4px solid #0f9d8f;color:#344054;
                            font-size:15px;line-height:1.7">
                  {safe_message}
                </div>
                <table role="presentation" cellspacing="0" cellpadding="0" style="margin:22px 0 12px">
                  <tr>
                    <td style="border-radius:12px;background:#0f9d8f">
                      <a href="{safe_workflow_url}"
                         style="display:inline-block;padding:13px 20px;color:#ffffff;text-decoration:none;
                                font-size:14px;font-weight:700">
                        Xem chi tiết hành trình
                      </a>
                    </td>
                  </tr>
                </table>
                <p style="margin:0;color:#667085;font-size:13px;line-height:1.65">
                  Liên kết này mở đúng yêu cầu dịch vụ vừa được cập nhật. Bạn có thể cần đăng nhập để xem.
                </p>
              </td>
            </tr>
            <tr>
              <td style="padding:18px 36px;background:#f8fafc;border-top:1px solid #eaecf0;
                         color:#98a2b3;font-size:12px;line-height:1.5">
                Email tự động từ P-118 · c3-app-118.io.vn
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
    return EmailContent(subject=subject, text=text, html=html_body)


async def _send_email_async(to_email: str, content: EmailContent) -> bool:
    settings = get_settings()

    if not settings.resend_api_key:
        if settings.app_env == "development":
            logger.warning(
                "Resend chưa cấu hình. MOCK EMAIL TO %s | Subject: %s | Body: %s",
                to_email,
                content.subject,
                content.text,
            )
        else:
            logger.error("Resend chưa cấu hình; email giao dịch không được gửi")
        return False

    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "from": f"{settings.resend_from_name} <{settings.resend_from_email}>",
        "to": [to_email],
        "subject": content.subject,
        "text": content.text,
        "html": content.html,
    }

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://api.resend.com/emails", json=payload, headers=headers, timeout=10.0)
            resp.raise_for_status()
            logger.info("Đã gửi email thành công tới %s (ID: %s)", to_email, resp.json().get("id"))
            return True
    except Exception as e:
        logger.error("Lỗi gửi email tới %s: %s", to_email, type(e).__name__)
        # Không raise để không làm crash API hoặc Workflow nếu chạy background
        return False


async def send_otp_email(to_email: str, otp_code: str) -> None:
    """Gửi OTP xác nhận qua email."""
    content = _otp_email(otp_code)
    await _send_email_async(to_email, content)


async def send_reset_password_email(to_email: str, otp_code: str) -> None:
    """Gửi OTP lấy lại mật khẩu qua email."""
    content = _reset_password_email(otp_code)
    await _send_email_async(to_email, content)


async def send_workflow_batch_email(
    to_email: str,
    assistant_message: str,
    workflow_id: str,
    task_id: str | None = None,
) -> None:
    """Gửi email thông báo kết quả duyệt tiến trình."""
    settings = get_settings()
    try:
        workflow_url = _workflow_detail_url(settings.frontend_base_url, workflow_id, task_id)
    except ValueError:
        logger.error("Không gửi email workflow: FRONTEND_BASE_URL không hợp lệ")
        return
    await _send_email_async(to_email, _workflow_update_email(assistant_message, workflow_url))
