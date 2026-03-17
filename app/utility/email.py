import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    def _send_email(to_email: str, subject: str, body: str, is_html: bool = False):
        if not settings.smtp_server:
            logger.warning(f"SMTP server not configured. Mocking email to {to_email} with subject: {subject}")
            return True

        msg = MIMEMultipart()
        msg['From'] = settings.smtp_from
        msg['To'] = to_email
        msg['Subject'] = subject

        msg.attach(MIMEText(body, 'html' if is_html else 'plain'))

        try:
            with smtplib.SMTP(settings.smtp_server, settings.smtp_port) as server:
                if settings.smtp_user and settings.smtp_password:
                    server.starttls()
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)
            logger.info(f"Email sent to {to_email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            return False

    @classmethod
    def send_verification_email(cls, to_email: str, token: str):
        subject = "Verify your FactorySenseAI Account"
        body = f"""
        <html>
            <body>
                <h2>Welcome to FactorySenseAI!</h2>
                <p>Please use the following token to verify your email address:</p>
                <p><strong>{token}</strong></p>
                <p>If you didn't create an account, you can safely ignore this email.</p>
            </body>
        </html>
        """
        return cls._send_email(to_email, subject, body, is_html=True)

    @classmethod
    def send_invitation_email(cls, to_email: str, invite_link: str, mill_name: str, role: str):
        subject = f"Invitation to join {mill_name} on FactorySenseAI"
        body = f"""
        <html>
            <body>
                <h2>You've been invited!</h2>
                <p>You have been invited to join <strong>{mill_name}</strong> as a <strong>{role}</strong>.</p>
                <p>Click the link below to set your password and join your team:</p>
                <p><a href="{invite_link}">{invite_link}</a></p>
                <p>This invitation will expire in 48 hours.</p>
            </body>
        </html>
        """
        return cls._send_email(to_email, subject, body, is_html=True)

    @classmethod
    def send_password_reset_email(cls, to_email: str, reset_link: str):
        subject = "Reset your FactorySenseAI Password"
        body = f"""
        <html>
            <body>
                <h2>Password Reset Request</h2>
                <p>You requested to reset your password. Click the link below to set a new password:</p>
                <p><a href="{reset_link}">{reset_link}</a></p>
                <p>This link will expire in 1 hour.</p>
                <p>If you didn't request this, you can safely ignore this email.</p>
            </body>
        </html>
        """
        return cls._send_email(to_email, subject, body, is_html=True)
