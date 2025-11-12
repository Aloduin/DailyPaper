import os
import sys
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import argparse
import requests

HF_API_URL = "https://huggingface.co/api/daily_papers"

def _date_str(dt):
    return dt.strftime("%Y-%m-%d")

def fetch_papers_for_date(date_str):
    url = f"{HF_API_URL}?date={date_str}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "items" in data:
        items = data.get("items") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    papers = []
    for it in items:
        paper = it.get("paper") if isinstance(it, dict) else None
        obj = paper if isinstance(paper, dict) else it if isinstance(it, dict) else {}
        title = obj.get("title") or obj.get("name") or ""
        authors = obj.get("authors") or []
        if isinstance(authors, list):
            names = []
            for a in authors:
                if isinstance(a, dict):
                    n = a.get("name") or a.get("fullName") or a.get("displayName")
                    if n:
                        names.append(n)
                elif isinstance(a, str):
                    names.append(a)
            authors_str = ", ".join(names)
        elif isinstance(authors, str):
            authors_str = authors
        else:
            authors_str = ""
        abstract = obj.get("abstract") or obj.get("summary") or obj.get("description") or ""
        url = obj.get("url") or obj.get("paperUrl") or obj.get("arxivUrl")
        if not url:
            pid = obj.get("id") or obj.get("paperId")
            if pid:
                url = f"https://huggingface.co/papers/{pid}"
        papers.append({
            "title": title,
            "authors": authors_str,
            "abstract": abstract,
            "url": url or ""
        })
    return papers

def get_daily_papers_with_fallback(tz_name, max_days_back=3):
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    for i in range(0, max_days_back + 1):
        d = today - timedelta(days=i)
        ds = _date_str(datetime(d.year, d.month, d.day))
        papers = fetch_papers_for_date(ds)
        if papers:
            return ds, papers
    return _date_str(datetime(today.year, today.month, today.day)), []

def build_email_html(date_str, papers):
    css = (
        "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:0;background:#f6f8fa;color:#24292e;}"
        ".container{max-width:820px;margin:0 auto;padding:24px;}"
        ".header{display:flex;align-items:center;gap:12px;margin-bottom:16px;}"
        ".badge{background:#2f81f7;color:#fff;font-weight:600;border-radius:999px;padding:4px 10px;font-size:12px;}"
        ".title{margin:0;font-size:22px;line-height:1.3;}"
        ".subtitle{margin:4px 0 0 0;color:#57606a;font-size:13px;}"
        ".card{background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:16px;margin:12px 0;box-shadow:0 1px 0 rgba(27,31,36,.04);}"
        ".paper-title{margin:0 0 8px;font-size:16px;color:#0969da;text-decoration:none;}"
        ".paper-title:hover{text-decoration:underline;}"
        ".meta{color:#57606a;font-size:13px;margin-bottom:8px;}"
        ".abstract{font-size:14px;line-height:1.55;color:#24292e;}"
        ".footer{margin-top:24px;color:#57606a;font-size:12px;}"
        ".empty{background:#fff; border:1px dashed #d0d7de; border-radius:10px; padding:20px; text-align:center; color:#57606a;}"
    )
    parts = []
    for p in papers:
        link = p.get("url") or ""
        title = p.get("title") or ""
        authors = p.get("authors") or ""
        abstract = p.get("abstract") or ""
        title_html = f"<a class=\"paper-title\" href=\"{link}\" target=\"_blank\">{title}</a>" if link else f"<div class=\"paper-title\">{title}</div>"
        parts.append(
            f"<div class=\"card\">{title_html}<div class=\"meta\">{authors}</div><div class=\"abstract\">{abstract}</div></div>"
        )
    body = (
        f"<div class=\"empty\">{date_str} 暂无可用的 Daily Papers。</div>" if not parts else "".join(parts)
    )
    html = (
        "<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<style>{css}</style></head>"
        "<body>"
        "<div class=\"container\">"
        f"<div class=\"header\"><span class=\"badge\">Daily</span><h1 class=\"title\">Hugging Face Daily Papers - {date_str}</h1></div>"
        f"<div class=\"content\">{body}</div>"
        "<div class=\"footer\">如需调整推送时间或筛选规则，请联系维护者。</div>"
        "</div>"
        "</body></html>"
    )
    return html

def build_email_text(date_str, papers):
    lines = [f"Hugging Face Daily Papers - {date_str}"]
    if not papers:
        lines.append(f"{date_str} 暂无可用的 Daily Papers。")
    else:
        for p in papers:
            title = p.get("title") or ""
            authors = p.get("authors") or ""
            abstract = p.get("abstract") or ""
            url = p.get("url") or ""
            lines.append(f"- {title}")
            if authors:
                lines.append(f"  作者: {authors}")
            if abstract:
                lines.append(f"  摘要: {abstract}")
            if url:
                lines.append(f"  链接: {url}")
    return "\n".join(lines)

def send_email(subject, html_body, text_body):
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "0") or 0)
    smtp_user = os.getenv("SMTP_USERNAME", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    mail_from = os.getenv("MAIL_FROM") or smtp_user
    mail_to_raw = os.getenv("MAIL_TO", "")
    recipients = [x.strip() for x in mail_to_raw.replace(";", ",").split(",") if x.strip()]
    if not smtp_host or not smtp_port or not smtp_user or not smtp_pass or not recipients:
        raise RuntimeError("邮件发送配置不完整")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = ", ".join(recipients)
    part1 = MIMEText(text_body, "plain", "utf-8")
    part2 = MIMEText(html_body, "html", "utf-8")
    msg.attach(part1)
    msg.attach(part2)
    use_ssl = smtp_port == 465
    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(mail_from, recipients, msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except Exception:
                pass
            server.login(smtp_user, smtp_pass)
            server.sendmail(mail_from, recipients, msg.as_string())

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="")
    parser.add_argument("--timezone", type=str, default=os.getenv("TIMEZONE", "Asia/Shanghai"))
    parser.add_argument("--max-days-back", type=int, default=int(os.getenv("MAX_DAYS_BACK", "3")))
    parser.add_argument("--subject-prefix", type=str, default=os.getenv("SUBJECT_PREFIX", ""))
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    if args.test:
        now = datetime.now(ZoneInfo(args.timezone))
        date_str = now.strftime("%Y-%m-%d %H:%M:%S %Z")
        subject = f"{args.subject_prefix}服务启动测试 - {date_str}".strip()
        html = (
            "<html><body><div style=\"font-family:Arial,Helvetica,sans-serif;max-width:680px;margin:0 auto;\">"
            f"<h2 style=\"color:#0969da\">服务启动测试</h2><p>时间：{date_str}</p><p>邮件发送链路验证成功。</p>"
            "</div></body></html>"
        )
        text = f"服务启动测试\n时间：{date_str}\n邮件发送链路验证成功。"
        send_email(subject, html, text)
        return

    tz_name = args.timezone
    if args.date:
        date_str = args.date
        papers = fetch_papers_for_date(date_str)
    else:
        date_str, papers = get_daily_papers_with_fallback(tz_name, args.max_days_back)
    subject_core = f"Hugging Face Daily Papers - {date_str}"
    subject = f"{args.subject_prefix}{subject_core}".strip()
    html = build_email_html(date_str, papers)
    text = build_email_text(date_str, papers)
    send_email(subject, html, text)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)