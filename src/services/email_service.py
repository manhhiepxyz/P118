"""
src/services/email_service.py
P-118 — Dịch vụ Gửi Email qua SMTP (Gmail)
"""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from src.config import get_settings

logger = logging.getLogger(__name__)


async def _send_email_async(to_email: str, subject: str, content: str) -> None:
    settings = get_settings()

    if not settings.smtp_username or not settings.smtp_password or not settings.smtp_host:
        logger.warning(
            f"SMTP chưa cấu hình. Fallback in ra console:\n"
            f"--- MOCK EMAIL TO {to_email} ---\n"
            f"Subject: {subject}\n"
            f"Body:\n{content}\n"
            f"----------------------------------"
        )
        return

    msg = MIMEMultipart()
    msg["From"] = settings.smtp_username
    msg["To"] = to_email
    msg["Subject"] = subject

    msg.attach(MIMEText(content, "html", "utf-8"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            start_tls=True,
            username=settings.smtp_username,
            password=settings.smtp_password,
        )
        logger.info(f"Đã gửi email thành công tới {to_email}")
    except Exception as e:
        logger.error(f"Lỗi gửi email tới {to_email}: {str(e)}")
        # Không raise để không làm crash API hoặc Workflow nếu chạy background


async def send_otp_email(to_email: str, otp_code: str) -> None:
    """Gửi email chứa mã OTP để xác thực tài khoản."""
    subject = "Mã xác nhận đăng ký tài khoản (AI20K Agent)"
    content = f"""
    <html>
        <body>
            <h3>Chào bạn,</h3>
            <p>Mã xác thực (OTP) để đăng ký tài khoản của bạn là: <strong style="font-size: 20px; color: blue;">{otp_code}</strong></p>
            <p>Mã này sẽ hết hạn trong vòng 5 phút.</p>
            <p>Nếu bạn không thực hiện yêu cầu này, vui lòng bỏ qua email.</p>
            <br>
            <p>Trân trọng,</p>
            <p><b>Hệ thống AI20K Agent</b></p>
        </body>
    </html>
    """
    await _send_email_async(to_email, subject, content)


async def send_workflow_batch_email(to_email: str, assistant_message: str) -> None:
    """Gửi email thông báo kết quả duyệt tiến trình."""
    subject = "Cập nhật yêu cầu dịch vụ của bạn (AI20K Agent)"

    # Chuyển đổi message của AI thành HTML cho dễ đọc (đơn giản hoá việc đổi \n thành <br>)
    html_message = assistant_message.replace("\n", "<br>")

    content = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
                <h3 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px;">
                    Thông báo cập nhật yêu cầu
                </h3>
                <p>Chào bạn,</p>
                <p>Hệ thống vừa có cập nhật mới về tiến trình yêu cầu dịch vụ của bạn. Trợ lý AI có lời nhắn dành cho bạn như sau:</p>

                <div style="background-color: #f9f9f9; border-left: 4px solid #3498db; padding: 15px; margin: 20px 0; font-size: 15px;">
                    {html_message}
                </div>

                <p style="font-size: 14px; color: #555;">(Nếu có yêu cầu thanh toán, vui lòng làm theo hướng dẫn ở trên để hoàn tất thủ tục).</p>

                <br>
                <p>Trân trọng,</p>
                <p><b>Ban quản lý & Trợ lý AI20K</b></p>
            </div>
        </body>
    </html>
    """
    await _send_email_async(to_email, subject, content)
