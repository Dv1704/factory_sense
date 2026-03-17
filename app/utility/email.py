import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
import os
import time
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.core.config import settings

logger = logging.getLogger(__name__)

# Initialize Jinja2 environment
template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
template_env = Environment(
    loader=FileSystemLoader(template_dir),
    autoescape=select_autoescape(['html', 'xml'])
)

class EmailService:
    @staticmethod
    def _render_template(template_name: str, **context):
        template = template_env.get_template(template_name)
        context['current_year'] = datetime.now().year
        return template.render(**context)

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

        max_retries = 3
        retry_delay = 2 # seconds
        
        for attempt in range(max_retries):
            try:
                with smtplib.SMTP(settings.smtp_server, settings.smtp_port, timeout=10) as server:
                    if settings.smtp_user and settings.smtp_password:
                        server.starttls()
                        server.login(settings.smtp_user, settings.smtp_password)
                    server.send_message(msg)
                logger.info(f"Email sent to {to_email} on attempt {attempt + 1}")
                return True
            except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, ConnectionRefusedError) as e:
                logger.warning(f"Transient SMTP error on attempt {attempt + 1} for {to_email}: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    logger.error(f"Max retries reached for {to_email}. Failed to send email.")
            except Exception as e:
                logger.error(f"Permanent or unexpected failure sending email to {to_email}: {str(e)}")
                break # Don't retry for non-transient errors
        
        return False

    @classmethod
    def send_verification_email(cls, to_email: str, token: str):
        subject = "Verify your FactorySenseAI Account"
        body = cls._render_template("email/verification.html", token=token)
        return cls._send_email(to_email, subject, body, is_html=True)

    @classmethod
    def send_invitation_email(cls, to_email: str, invite_link: str, mill_name: str, role: str):
        subject = f"Invitation to join {mill_name} on FactorySenseAI"
        body = cls._render_template("email/invitation.html", 
                                   invite_link=invite_link, 
                                   mill_name=mill_name, 
                                   role=role)
        return cls._send_email(to_email, subject, body, is_html=True)

    @classmethod
    def send_password_reset_email(cls, to_email: str, reset_link: str):
        subject = "Reset your FactorySenseAI Password"
        body = cls._render_template("email/password_reset.html", reset_link=reset_link)
        return cls._send_email(to_email, subject, body, is_html=True)
