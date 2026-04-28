#!/usr/bin/env python3
"""
Gmail Fetcher — Downloads Dhan trade-confirmation PDFs from Gmail.

Security hardening:
  - IMAP over TLS (port 993, imaplib.IMAP4_SSL)
  - Auth via Gmail App Password (NOT real password); requires 2FA on the account
  - 30s connection timeout; never hangs
  - Email content & attachment bytes are NEVER logged
  - SHA-256 dedup via .processed manifest in Trades log/
  - Sanitizes filename: strips PAN-like patterns before saving
  - Reads credentials only from env vars (never CLI / never hardcoded)

Usage:
    # uses defaults from .env
    python gmail_fetcher.py

    # only fetch emails from a date forward
    python gmail_fetcher.py --since 2026-03-10

    # change download folder
    python gmail_fetcher.py --out "Trades log"
"""
from __future__ import annotations

import argparse
import email
import hashlib
import imaplib
import os
import re
import sys
from datetime import datetime
from email.header import decode_header
from pathlib import Path
from typing import Iterable, Optional

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
TIMEOUT_SECS = 30
MANIFEST_NAME = ".processed"


def _load_dotenv_lazy() -> None:
    """Tiny .env loader (no external deps). Only sets vars not already set."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for piece, charset in parts:
        if isinstance(piece, bytes):
            try:
                out.append(piece.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                out.append(piece.decode("utf-8", errors="replace"))
        else:
            out.append(piece)
    return "".join(out)


# Strip anything that looks like a PAN (5 letters + 4 digits + 1 letter,
# or partially-redacted variants like CJRXXXXX2H) from the filename.
_PAN_RE = re.compile(r"[A-Z]{3,5}[A-Z0-9*X]{4,5}[0-9][A-Z]", re.IGNORECASE)


def _sanitize_filename(name: str) -> str:
    name = os.path.basename(name)
    name = _PAN_RE.sub("REDACTED", name)
    # remove control / path chars
    name = re.sub(r"[^\w.\-+]", "_", name)
    return name.strip("._") or "trade.pdf"


def _build_target_filename(email_date: datetime, raw_name: str, pdf_bytes: bytes) -> str:
    short_hash = hashlib.sha256(pdf_bytes).hexdigest()[:10]
    ext = ".pdf"
    if "." in raw_name:
        ext = "." + raw_name.rsplit(".", 1)[-1].lower()
        if ext != ".pdf":
            ext = ".pdf"
    return f"trade_{email_date.strftime('%Y-%m-%d')}_{short_hash}{ext}"


def _load_manifest(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {ln.strip() for ln in path.read_text().splitlines() if ln.strip()}


def _append_manifest(path: Path, content_hash: str) -> None:
    with path.open("a") as f:
        f.write(content_hash + "\n")


def _imap_search(
    imap: imaplib.IMAP4_SSL,
    sender: str,
    subject_keyword: str,
    since: Optional[datetime],
) -> list[bytes]:
    criteria: list[str] = ['FROM', f'"{sender}"']
    if subject_keyword:
        criteria += ['SUBJECT', f'"{subject_keyword}"']
    if since is not None:
        criteria += ['SINCE', since.strftime("%d-%b-%Y")]
    typ, data = imap.search(None, *criteria)
    if typ != "OK":
        return []
    if not data or not data[0]:
        return []
    return data[0].split()


def _iter_pdf_attachments(msg) -> Iterable[tuple[str, bytes]]:
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = _decode(part.get_filename())
        ctype = part.get_content_type() or ""
        is_pdf = (
            (filename and filename.lower().endswith(".pdf"))
            or ctype.lower() == "application/pdf"
        )
        if not is_pdf:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        yield filename or "attachment.pdf", payload


def fetch_pdfs(
    out_dir: Path,
    since: Optional[datetime] = None,
    sender: Optional[str] = None,
    subject_keyword: Optional[str] = None,
) -> int:
    _load_dotenv_lazy()

    user = os.environ.get("GMAIL_ADDRESS", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    # Defaults verified against this account in Apr 2026.
    # Treat an explicitly-empty env var (common in CI when an optional secret
    # isn't set) the same as "missing", and fall through to the default.
    sender = sender or (os.environ.get("DHAN_SENDER") or "").strip() or "statements@dhan.co"
    if subject_keyword is None:
        subject_keyword = (os.environ.get("DHAN_SUBJECT_KEYWORD") or "").strip() or "Contract Note"

    if not user or not pw:
        print(
            "ERROR: GMAIL_ADDRESS / GMAIL_APP_PASSWORD env vars are not set.",
            file=sys.stderr,
        )
        print(
            "Set them in .env or as GitHub Actions Secrets. App Password setup:\n"
            "  https://myaccount.google.com/apppasswords",
            file=sys.stderr,
        )
        return 0
    if user == "your@gmail.com" or pw.startswith("xxxx"):
        print("ERROR: GMAIL_ADDRESS / GMAIL_APP_PASSWORD still hold placeholder values.", file=sys.stderr)
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / MANIFEST_NAME
    seen = _load_manifest(manifest_path)

    print(f"Connecting to {IMAP_HOST}:{IMAP_PORT} as {user[:3]}***@{user.split('@')[-1]}")
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=TIMEOUT_SECS)
    new_count = 0
    try:
        imap.login(user, pw)
        # Search "All Mail" so we catch archived/labeled emails, not just inbox.
        # Falls back to INBOX on accounts where All Mail isn't exposed (rare).
        for folder in ('"[Gmail]/All Mail"', "INBOX"):
            typ, _ = imap.select(folder, readonly=True)
            if typ == "OK":
                break
        ids = _imap_search(imap, sender, subject_keyword, since)
        print(f"Found {len(ids)} candidate email(s) from {sender}")
        for msg_id in ids:
            typ, data = imap.fetch(msg_id, "(RFC822)")
            if typ != "OK" or not data or not data[0]:
                continue
            raw = data[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            msg = email.message_from_bytes(raw)
            try:
                email_date = email.utils.parsedate_to_datetime(msg.get("Date") or "")
            except Exception:
                email_date = datetime.now()
            for raw_name, pdf_bytes in _iter_pdf_attachments(msg):
                content_hash = hashlib.sha256(pdf_bytes).hexdigest()
                if content_hash in seen:
                    continue
                target_name = _sanitize_filename(
                    _build_target_filename(email_date, raw_name, pdf_bytes)
                )
                target_path = out_dir / target_name
                # write atomically: tmp then rename
                tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
                tmp_path.write_bytes(pdf_bytes)
                tmp_path.replace(target_path)
                seen.add(content_hash)
                _append_manifest(manifest_path, content_hash)
                new_count += 1
                print(f"  + {target_name}  ({len(pdf_bytes)} bytes)")
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    print(f"Done. {new_count} new PDF(s) saved to {out_dir}")
    return new_count


def _parse_since(raw: str) -> datetime:
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--since must be YYYY-MM-DD, got: {raw!r}"
        ) from exc


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch Dhan trade PDFs from Gmail.")
    parser.add_argument(
        "--out", default="Trades log", help="Folder to save PDFs (default: 'Trades log')"
    )
    parser.add_argument(
        "--since",
        type=_parse_since,
        default=None,
        help="Only fetch emails on/after this date (YYYY-MM-DD)",
    )
    parser.add_argument("--sender", default=None, help="Override DHAN_SENDER")
    parser.add_argument(
        "--subject", default=None, help="Override DHAN_SUBJECT_KEYWORD ('' for none)"
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out).resolve()
    fetch_pdfs(
        out_dir=out_dir,
        since=args.since,
        sender=args.sender,
        subject_keyword=args.subject,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
