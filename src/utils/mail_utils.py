import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

from src.config.job_config import MAIL_FROM, MAIL_PASSWORD, SMTP_PORT, SMTP_SERVER


def parse_mail_recipients(raw):
    if not raw:
        return []
    return [r.strip() for r in raw.split(",") if r.strip()]


def mask_email(addr):
    if "@" not in addr:
        return "***"
    local, domain = addr.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


def send_html_email(html, subject, to_list, cc_list=None, bcc_list=None):
    cc_list = cc_list or []
    bcc_list = bcc_list or []
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("Daily News Bot", MAIL_FROM))
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    recipients = to_list + cc_list + bcc_list
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
        s.starttls()
        s.login(MAIL_FROM, MAIL_PASSWORD)
        s.sendmail(MAIL_FROM, recipients, msg.as_string())
