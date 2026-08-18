"""HTML email templates matching the StikkVerse dashboard look (dark navy, cyan accent)."""

_BG = "#0a0e17"
_CARD_BG = "#0f1626"
_BORDER = "rgba(34,211,238,0.25)"
_CYAN = "#22d3ee"
_TEXT = "#e2e8f0"
_MUTED = "#94a3b8"
_MONO = "'JetBrains Mono','Courier New',monospace"
_SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"


def _shell(preheader: str, heading: str, body_html: str, cta_text: str = None, cta_url: str = None) -> str:
    cta_html = ""
    if cta_text and cta_url:
        cta_html = f"""
        <tr><td style="padding:28px 0 4px;">
          <a href="{cta_url}" style="display:inline-block;padding:14px 32px;border-radius:8px;
             background:linear-gradient(135deg,#22d3ee,#6366f1);color:#0a0e17;font-family:{_SANS};
             font-weight:700;font-size:14px;text-decoration:none;">{cta_text}</a>
        </td></tr>"""

    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:{_BG};">
<span style="display:none;max-height:0;overflow:hidden;">{preheader}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};padding:40px 16px;">
<tr><td align="center">
<table role="presentation" width="480" cellpadding="0" cellspacing="0"
       style="background:{_CARD_BG};border:1px solid {_BORDER};border-radius:12px;overflow:hidden;">
  <tr><td style="padding:28px 32px 0;">
    <div style="font-family:{_SANS};color:#ffffff;font-size:16px;font-weight:700;">StikkVerse</div>
    <div style="font-family:{_MONO};color:{_CYAN};font-size:10px;letter-spacing:2px;">VITALS MONITOR</div>
  </td></tr>
  <tr><td style="padding:24px 32px 0;">
    <div style="width:32px;height:2px;background:{_CYAN};margin-bottom:16px;"></div>
    <h1 style="margin:0;font-family:{_SANS};color:#ffffff;font-size:26px;font-weight:700;">{heading}</h1>
  </td></tr>
  <tr><td style="padding:16px 32px 0;font-family:{_SANS};color:{_TEXT};font-size:14px;line-height:1.6;">
    {body_html}
  </td></tr>
  <tr><td style="padding:0 32px;">{cta_html}</td></tr>
  <tr><td style="padding:32px 32px 24px;border-top:1px solid {_BORDER};margin-top:24px;">
    <div style="font-family:{_MONO};color:{_MUTED};font-size:10px;letter-spacing:1px;padding-top:20px;">
      StikkVerse &middot; Machine Health &amp; Telemetry Platform
    </div>
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def welcome_email(mill_id: str, api_key: str) -> str:
    body = f"""
    <p style="margin:0 0 16px;">Your account is ready. Here's your API key for mill
    <strong style="color:#fff;">{mill_id}</strong> — it authenticates your data uploads, so keep it secret.</p>
    <div style="margin:8px 0 4px;font-family:{_MONO};color:{_MUTED};font-size:11px;letter-spacing:1px;">API KEY</div>
    <div style="font-family:{_MONO};color:{_CYAN};font-size:14px;background:#0a0e17;
                border:1px solid {_BORDER};border-radius:6px;padding:12px 14px;word-break:break-all;">{api_key}</div>
    """
    return _shell("Your StikkVerse account is ready.", "Welcome back.", body)


def magic_link_email(login_link: str) -> str:
    body = """
    <p style="margin:0;">Click below to sign in instantly -- no password needed. This link is
    valid for 15 minutes and works once. If you didn't request this, you can safely ignore it.</p>
    """
    return _shell(
        "Your StikkVerse sign-in link.",
        "Sign in to StikkVerse.",
        body,
        cta_text="SIGN IN",
        cta_url=login_link,
    )


def verification_email(verify_link: str) -> str:
    body = """
    <p style="margin:0;">One last step -- confirm this is your email address. This link is
    valid for 24 hours. If you didn't create a StikkVerse account, you can ignore this email.</p>
    """
    return _shell(
        "Verify your StikkVerse email.",
        "Verify your email.",
        body,
        cta_text="VERIFY EMAIL",
        cta_url=verify_link,
    )


def password_reset_email(reset_link: str) -> str:
    body = """
    <p style="margin:0;">We received a request to reset your password. This link is valid for
    1 hour. If you didn't request this, you can safely ignore this email.</p>
    """
    return _shell(
        "Reset your StikkVerse password.",
        "Reset your password.",
        body,
        cta_text="RESET PASSWORD",
        cta_url=reset_link,
    )
