from __future__ import annotations

import html
import re
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\x0b\r\f]+")
_MULTI_NL = re.compile(r"\n{3,}")

def _html_to_text(html_body: str) -> str:
    # very small HTML -> text conversion (enough to provide a text/plain part)
    s = html_body or ""
    s = s.replace("\r", "")
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</p\s*>", "\n\n", s)
    s = re.sub(r"(?i)<p\b[^>]*>", "", s)
    s = re.sub(r"(?i)</li\s*>", "\n", s)
    s = re.sub(r"(?i)<li\b[^>]*>", "• ", s)
    s = re.sub(r"(?i)</(div|h\d|ul|ol|table|tr)>", "\n", s)
    s = re.sub(r"(?i)<hr\b[^>]*>", "\n----------------\n", s)
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", s)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    s = s.replace("\u00a0", " ")
    s = _WS_RE.sub(" ", s)
    s = s.strip()
    s = s.replace(" \n", "\n").replace("\n ", "\n")
    s = _MULTI_NL.sub("\n\n", s)
    return s if s else "(no content)"

def send_html_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    mail_from: str,
    to_email: str,
    subject: str,
    html_body: str,
    timeout_seconds: int = 30,
) -> None:
    """
    Sends a standards-compliant email:
    - Adds Date + Message-ID
    - Sends multipart/alternative (text/plain + text/html)
    - Uses a stable EHLO/HELO hostname derived from From domain (helps deliverability)
    """
    display_from = mail_from.strip()
    from_name, from_addr = parseaddr(display_from)
    envelope_from = from_addr or display_from

    # Prefer a deterministic Message-ID domain
    msgid_domain = None
    if "@" in envelope_from:
        msgid_domain = envelope_from.split("@", 1)[1].strip() or None

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = display_from
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=msgid_domain)
    msg["Reply-To"] = envelope_from
    # Hint that it's automated (often helps avoid auto-replies / loops)
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"

    text_body = _html_to_text(html_body)
    msg.set_content(text_body, subtype="plain", charset="utf-8")
    msg.add_alternative(html_body or "", subtype="html", charset="utf-8")

    # Better EHLO than container hostname: use from-domain if possible
    local_hostname = msgid_domain or None

    timeout = int(timeout_seconds)
    if int(smtp_port) == 465:
        with smtplib.SMTP_SSL(smtp_host, int(smtp_port), timeout=timeout, local_hostname=local_hostname) as server:
            server.ehlo()
            if smtp_user:
                server.login(smtp_user, smtp_pass)
            server.sendmail(envelope_from, [to_email], msg.as_bytes())
    else:
        with smtplib.SMTP(smtp_host, int(smtp_port), timeout=timeout, local_hostname=local_hostname) as server:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except Exception:
                pass
            if smtp_user:
                server.login(smtp_user, smtp_pass)
            server.sendmail(envelope_from, [to_email], msg.as_bytes())
