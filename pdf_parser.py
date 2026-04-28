#!/usr/bin/env python3
"""
PDF Parser — Reads Dhan trade-confirmation PDFs and produces TradesFromPDFs.csv.

Pipeline:
  1. For each *.pdf in 'Trades log/':
     - Open with PDF_PASSWORD (PAN) from env
     - Extract text
     - Pull executed-order rows: (symbol, side, timestamp, qty, price, net_amount, status)
     - Discard rows where status != FILLED or net_amount == 0
  2. Group by symbol + trade-date.
  3. FIFO-pair entries with exits (BUY ↔ SELL) — preserves partial fills.
  4. Compute pnlPoints, pnlRupees, duration.
  5. Merge with existing TradesFromPDFs.csv (dedup by entry_ts + symbol + qty).
  6. Re-sort all rows by entry time, renumber, write out.

Inspect / debug:
    python pdf_parser.py --inspect Trades\\ log/<file>.pdf
    # → prints raw extracted text for one PDF (no CSV write)

Run:
    python pdf_parser.py
    python pdf_parser.py --in "Trades log" --out TradesFromPDFs.csv

Security:
  - PDF_PASSWORD read from env only (loaded from .env if present)
  - No PII (name / PAN) is written into the CSV — only futures symbol + numbers
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, date as date_cls
from pathlib import Path
from typing import Iterable, Optional

try:
    import pymupdf  # type: ignore
except ImportError as exc:  # pragma: no cover
    print(
        "ERROR: pymupdf is not installed. Run: pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _load_dotenv_lazy() -> None:
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


_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")

# Month index → canonical 3-letter abbreviation (matches TradesFromPDFs.csv)
_MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

_FUT_SYMBOLS = {"BANKNIFTY", "NIFTY", "FINNIFTY", "MIDCPNIFTY", "BANKEX", "SENSEX"}

# Time format Dhan uses in contract notes: 12-hour with AM/PM
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}\s*(AM|PM|am|pm)$")
# Dhan trade-no: 17–20 digits starting with YYYYMMDD
_TRADE_NO_RE = re.compile(r"^\d{15,22}$")
# Dhan expiry: DD/MM/YYYY
_EXPIRY_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def _to_float(token: str) -> Optional[float]:
    token = token.replace(",", "").replace("\u20b9", "").strip()
    try:
        return float(token)
    except ValueError:
        return None


def _to_int(token: str) -> Optional[int]:
    token = token.replace(",", "").strip()
    if not token.lstrip("-").isdigit():
        return None
    try:
        return int(token)
    except ValueError:
        return None


def normalize_symbol_with_expiry(raw_symbol: str, expiry_token: str) -> Optional[str]:
    """Build canonical "<INDEX> <MMM> FUT" from Dhan symbol + DD/MM/YYYY expiry."""
    if not raw_symbol:
        return None
    sym = raw_symbol.strip().upper()
    if sym not in _FUT_SYMBOLS:
        return None
    m = _EXPIRY_RE.match(expiry_token.strip())
    if not m:
        return None
    month = int(m.group(2))
    if month not in _MONTH_ABBR:
        return None
    return f"{sym} {_MONTH_ABBR[month]} FUT"


def normalize_symbol(raw: str) -> Optional[str]:
    """Best-effort normalization for free-form text (used by debug tools/tests).

    Recognizes patterns like ``BANKNIFTY26FEBFUT`` or ``NIFTY-Mar2026-FUT`` that
    show up in some legacy fixtures. Real Dhan PDFs flow through
    ``normalize_symbol_with_expiry`` instead.
    """
    if not raw:
        return None
    text = raw.upper()
    # Find the underlying first
    sym = None
    for cand in _FUT_SYMBOLS:
        if cand in text:
            sym = cand
            break
    if sym is None:
        return None
    # Then look for a month abbreviation
    m = re.search(
        r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b",
        text,
    )
    if not m:
        return None
    mon = m.group(1)
    if "FUT" not in text:
        return None
    return f"{sym} {mon} FUT"


# ─────────────────────────────────────────────────────────────────────────────
# Order = a single executed leg parsed from a PDF.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Order:
    timestamp: datetime
    symbol: str          # canonical "BANKNIFTY MAR FUT"
    side: str            # "BUY" or "SELL"
    qty: int
    price: float
    net_amount: float    # |qty * price| — used for filtering 0-amount lines
    status: str          # "FILLED" / "CANCELLED" / "" (best-effort)
    source_pdf: str

    @property
    def trade_date(self) -> date_cls:
        return self.timestamp.date()


# ─────────────────────────────────────────────────────────────────────────────
# PDF text extraction
# ─────────────────────────────────────────────────────────────────────────────

def _open_pdf(path: Path, password: str):
    doc = pymupdf.open(path)
    if doc.needs_pass:
        if not password:
            raise RuntimeError(
                f"{path.name} is password-protected and PDF_PASSWORD env var is empty"
            )
        # Try a few common PAN variations
        for candidate in (password, password.upper(), password.lower(), password.strip()):
            if doc.authenticate(candidate):
                break
        else:
            raise RuntimeError(f"PDF_PASSWORD did not unlock {path.name}")
    return doc


def extract_text(path: Path, password: str) -> str:
    doc = _open_pdf(path, password)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


# ─────────────────────────────────────────────────────────────────────────────
# Order extraction — Dhan F&O contract-note layout.
#
# pymupdf returns the table as a vertical token stream (one cell per line).
# Each row of the F&O table follows a fixed schema, so we tokenize the text
# and walk each row anchored on the literal "FUTIDX" cell:
#
#   ...
#   <SrNo>
#   <TM Name line 1>
#   <TM Name line 2>
#   <Client Code>
#   <Margin / mode flag>     ← typically "M"
#   <Side>                   ← "B" or "S"
#   FUTIDX                   ← anchor
#   <Symbol>                 ← BANKNIFTY / NIFTY / ...
#   <Expiry>                 ← DD/MM/YYYY  (drives the canonical "MAR FUT")
#   <Option Type>            ← "FF" for futures
#   <Strike>                 ← "0.00" for futures
#   <Trade No>               ← 17–20 digits, first 8 = YYYYMMDD = trade date
#   <Trade Time>             ← "HH:MM:SS AM/PM"
#   <Quantity>
#   <Price>
#   <Traded Value>
#
# We pull the side from the line above the anchor and the rest from the
# lines immediately after, with light validation of each cell type.
# ─────────────────────────────────────────────────────────────────────────────

_FUT_ANCHOR = "FUTIDX"


def _tokenize(text: str) -> list[str]:
    """Split PDF text into non-empty trimmed lines."""
    lines: list[str] = []
    for raw in text.replace("\u00a0", " ").splitlines():
        s = raw.strip()
        if s:
            lines.append(s)
    return lines


def _parse_dhan_time(date_yyyymmdd: str, time_token: str) -> Optional[datetime]:
    if not _TIME_RE.match(time_token):
        return None
    try:
        d = datetime.strptime(date_yyyymmdd, "%Y%m%d").date()
    except ValueError:
        return None
    try:
        t = datetime.strptime(time_token.upper(), "%I:%M:%S %p").time()
    except ValueError:
        return None
    return datetime.combine(d, t)


def _find_side_above(tokens: list[str], anchor_idx: int) -> Optional[str]:
    """Walk back up to 3 tokens from FUTIDX and pick the first B/S cell."""
    for back in range(1, 4):
        i = anchor_idx - back
        if i < 0:
            return None
        tok = tokens[i].upper()
        if tok == "B":
            return "BUY"
        if tok == "S":
            return "SELL"
    return None


def parse_orders_from_text(text: str, source_pdf: str) -> list[Order]:
    if not text or not text.strip():
        return []

    tokens = _tokenize(text)
    orders: list[Order] = []
    seen_keys: set[tuple] = set()

    for i, tok in enumerate(tokens):
        if tok != _FUT_ANCHOR:
            continue

        side = _find_side_above(tokens, i)
        if side is None:
            continue

        # Schema after FUTIDX (offset → meaning)
        #   +1 SYMBOL   +2 EXPIRY   +3 FF   +4 STRIKE   +5 TRADE_NO
        #   +6 TIME     +7 QTY      +8 PRICE   +9 VALUE
        if i + 9 >= len(tokens):
            continue
        symbol_raw = tokens[i + 1]
        expiry_raw = tokens[i + 2]
        opt_type = tokens[i + 3]
        trade_no = tokens[i + 5]
        time_raw = tokens[i + 6]
        qty_raw = tokens[i + 7]
        price_raw = tokens[i + 8]

        if opt_type.upper() != "FF":
            # Not a futures row (would be CE/PE for options) — skip.
            continue
        if not _TRADE_NO_RE.match(trade_no):
            continue

        canonical = normalize_symbol_with_expiry(symbol_raw, expiry_raw)
        if not canonical:
            continue

        ts = _parse_dhan_time(trade_no[:8], time_raw)
        if ts is None:
            continue

        qty = _to_int(qty_raw)
        price = _to_float(price_raw)
        if not qty or qty <= 0 or price is None or price <= 0:
            continue

        net_amount = qty * price
        key = (ts.replace(microsecond=0), canonical, side, qty, round(price, 2), trade_no)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        orders.append(
            Order(
                timestamp=ts,
                symbol=canonical,
                side=side,
                qty=qty,
                price=price,
                net_amount=net_amount,
                status="FILLED",
                source_pdf=source_pdf,
            )
        )

    return orders


# ─────────────────────────────────────────────────────────────────────────────
# Contract-Note ("YACK..._Contract_Note_Eqfo_signed.pdf") layout.
#
# This is the full SEBI contract note Dhan emails as
# "Contract Note (Cash and F&O) - Trade Date <DD/MM/YYYY>" from
# statements@dhan.co. The Trade Annexure (last page) has, per row:
#
#   <Order Number>
#   <Order Time>          HH:MM:SS  (24-hour)
#   <Trade No>
#   <Trade Time>          HH:MM:SS  (24-hour)
#   <Security/Contract>   "FUTIDX BANKNIFTY 28Apr2026 - NSE"  (one line)
#   <Buy/Sell>            "B" / "S"
#   <Quantity>
#   <Price>
#   <Net Rate>
#   <Net Amount>
#   [Remark — optional, often blank]
#
# We anchor each row on the security-description line, read time from the
# preceding line (offset −1), side/qty/price from offsets +1/+2/+3.
# The trade date comes from page 1 ("Contract Date : DD-MM-YYYY").
# ─────────────────────────────────────────────────────────────────────────────

_CONTRACT_NOTE_SYM_RE = re.compile(
    r"^(?P<inst>FUTIDX|FUTSTK|FUTCOM)\s+"
    r"(?P<sym>[A-Z]+)\s+"
    r"(?P<day>\d{1,2})(?P<mon>[A-Za-z]{3})(?P<year>\d{4})"
    r"\s+-\s+(?P<exch>NSE|BSE|MCX|NCDEX)$",
    re.MULTILINE,
)

_CONTRACT_DATE_RE = re.compile(
    r"(?:Contract Date|Trade Date)\s*:\s*(\d{2}-\d{2}-\d{4})"
)

_HHMMSS_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")

_MONTH_3_FROM_TEXT = {
    "JAN": "JAN", "FEB": "FEB", "MAR": "MAR", "APR": "APR", "MAY": "MAY", "JUN": "JUN",
    "JUL": "JUL", "AUG": "AUG", "SEP": "SEP", "OCT": "OCT", "NOV": "NOV", "DEC": "DEC",
}


def _extract_contract_date(text: str) -> Optional[date_cls]:
    m = _CONTRACT_DATE_RE.search(text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d-%m-%Y").date()
    except ValueError:
        return None


def parse_contract_note_text(
    text: str,
    source_pdf: str,
    *,
    include_stock_futures: bool = True,
    include_commodity: bool = True,
) -> list[Order]:
    if not text or not text.strip():
        return []

    contract_date = _extract_contract_date(text)
    if contract_date is None:
        return []

    lines = _tokenize(text)
    orders: list[Order] = []
    seen_keys: set[tuple] = set()

    for i, line in enumerate(lines):
        m = _CONTRACT_NOTE_SYM_RE.match(line)
        if not m:
            continue
        inst = m.group("inst")
        underlying = m.group("sym").upper()
        mon_raw = m.group("mon").upper()

        if inst == "FUTSTK" and not include_stock_futures:
            continue
        if inst == "FUTCOM" and not include_commodity:
            continue
        # FUTIDX → restrict to known index underlyings (BANKNIFTY/NIFTY/FINNIFTY...)
        # FUTSTK / FUTCOM → accept any ticker; the broker's PDF is authoritative.
        if inst == "FUTIDX" and underlying not in _FUT_SYMBOLS:
            continue
        mon_abbr = _MONTH_3_FROM_TEXT.get(mon_raw)
        if mon_abbr is None:
            continue

        if i - 1 < 0 or i + 3 >= len(lines):
            continue

        time_token = lines[i - 1]
        if not _HHMMSS_RE.match(time_token):
            continue
        side_token = lines[i + 1].upper()
        if side_token == "B":
            side = "BUY"
        elif side_token == "S":
            side = "SELL"
        else:
            continue
        qty = _to_int(lines[i + 2])
        price = _to_float(lines[i + 3])
        if not qty or qty <= 0 or price is None or price <= 0:
            continue

        try:
            t = datetime.strptime(time_token, "%H:%M:%S").time()
        except ValueError:
            continue
        ts = datetime.combine(contract_date, t)

        canonical = f"{underlying} {mon_abbr} FUT"
        key = (ts.replace(microsecond=0), canonical, side, qty, round(price, 2))
        if key in seen_keys:
            continue
        seen_keys.add(key)

        orders.append(
            Order(
                timestamp=ts,
                symbol=canonical,
                side=side,
                qty=qty,
                price=price,
                net_amount=qty * price,
                status="FILLED",
                source_pdf=source_pdf,
            )
        )

    return orders


def parse_pdf(path: Path, password: str) -> list[Order]:
    """Auto-detect Dhan PDF layout and dispatch to the right parser."""
    text = extract_text(path, password)
    if _CONTRACT_NOTE_SYM_RE.search(text or ""):
        return parse_contract_note_text(text, source_pdf=path.name)
    return parse_orders_from_text(text, source_pdf=path.name)


# ─────────────────────────────────────────────────────────────────────────────
# FIFO matching
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MatchedTrade:
    symbol: str
    side: str               # LONG / SHORT (entry direction)
    entry_time: datetime
    exit_time: datetime
    qty: int
    entry_price: float
    exit_price: float

    @property
    def pnl_points(self) -> float:
        if self.side == "LONG":
            return round(self.exit_price - self.entry_price, 2)
        return round(self.entry_price - self.exit_price, 2)

    @property
    def pnl_rupees(self) -> float:
        return round(self.pnl_points * self.qty, 2)

    @property
    def duration_str(self) -> str:
        secs = int((self.exit_time - self.entry_time).total_seconds())
        if secs < 0:
            secs = 0
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m {s}s"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"


def fifo_match(orders: list[Order]) -> list[MatchedTrade]:
    """FIFO-pair BUY ↔ SELL within (symbol, trade_date).

    Each filled order is queued; opposing orders consume the queue in FIFO order
    and emit one MatchedTrade per filled chunk.
    """
    if not orders:
        return []

    by_bucket: dict[tuple[str, date_cls], list[Order]] = {}
    for o in orders:
        by_bucket.setdefault((o.symbol, o.trade_date), []).append(o)

    trades: list[MatchedTrade] = []

    for (symbol, _), bucket in by_bucket.items():
        bucket.sort(key=lambda o: o.timestamp)
        # Each open leg = (qty_remaining, price, timestamp)
        open_long: list[list] = []
        open_short: list[list] = []

        for o in bucket:
            if o.side == "BUY":
                # close any open shorts first (FIFO)
                qty_left = o.qty
                while qty_left > 0 and open_short:
                    head = open_short[0]
                    take = min(head[0], qty_left)
                    trades.append(
                        MatchedTrade(
                            symbol=symbol,
                            side="SHORT",
                            entry_time=head[2],
                            exit_time=o.timestamp,
                            qty=take,
                            entry_price=head[1],
                            exit_price=o.price,
                        )
                    )
                    head[0] -= take
                    qty_left -= take
                    if head[0] == 0:
                        open_short.pop(0)
                if qty_left > 0:
                    open_long.append([qty_left, o.price, o.timestamp])
            else:  # SELL
                qty_left = o.qty
                while qty_left > 0 and open_long:
                    head = open_long[0]
                    take = min(head[0], qty_left)
                    trades.append(
                        MatchedTrade(
                            symbol=symbol,
                            side="LONG",
                            entry_time=head[2],
                            exit_time=o.timestamp,
                            qty=take,
                            entry_price=head[1],
                            exit_price=o.price,
                        )
                    )
                    head[0] -= take
                    qty_left -= take
                    if head[0] == 0:
                        open_long.pop(0)
                if qty_left > 0:
                    open_short.append([qty_left, o.price, o.timestamp])

    trades.sort(key=lambda t: t.entry_time)
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# CSV merge + write
# ─────────────────────────────────────────────────────────────────────────────

CSV_HEADER = [
    "Trade #",
    "Symbol",
    "Side",
    "Entry Time",
    "Exit Time",
    "Qty",
    "Entry Price",
    "Exit Price",
    "P&L Points",
    "P&L (\u20b9)",
    "Duration",
]


def _row_key(symbol: str, entry_time_str: str, qty: int) -> tuple:
    return (symbol, entry_time_str.strip(), int(qty))


def _matched_to_row(t: MatchedTrade) -> list[str]:
    return [
        "",  # Trade # — assigned later
        t.symbol,
        t.side,
        t.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
        t.exit_time.strftime("%Y-%m-%d %H:%M:%S"),
        str(t.qty),
        f"{t.entry_price:.1f}" if t.entry_price >= 100 else f"{t.entry_price:.2f}",
        f"{t.exit_price:.1f}" if t.exit_price >= 100 else f"{t.exit_price:.2f}",
        f"{t.pnl_points:.1f}",
        f"{t.pnl_rupees:.1f}",
        t.duration_str,
    ]


def _read_existing(csv_path: Path) -> list[list[str]]:
    if not csv_path.exists():
        return []
    rows: list[list[str]] = []
    with csv_path.open() as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            return []
        for row in reader:
            if not row or len(row) < len(CSV_HEADER):
                continue
            rows.append(row)
    return rows


def write_merged_csv(csv_path: Path, new_trades: list[MatchedTrade]) -> tuple[int, int]:
    existing = _read_existing(csv_path)
    seen: set[tuple] = set()
    merged_rows: list[list[str]] = []

    for row in existing:
        key = _row_key(row[1], row[3], int(row[5]) if row[5].isdigit() else 0)
        if key in seen:
            continue
        seen.add(key)
        merged_rows.append(row)

    added = 0
    for t in new_trades:
        row = _matched_to_row(t)
        key = _row_key(row[1], row[3], int(row[5]))
        if key in seen:
            continue
        seen.add(key)
        merged_rows.append(row)
        added += 1

    # sort by entry time
    def _entry_dt(row: list[str]) -> datetime:
        try:
            return datetime.strptime(row[3], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.min

    merged_rows.sort(key=_entry_dt)
    for i, row in enumerate(merged_rows, start=1):
        row[0] = str(i)

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        writer.writerows(merged_rows)

    return len(merged_rows), added


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _iter_pdfs(folder: Path) -> Iterable[Path]:
    if not folder.exists():
        return
    for p in sorted(folder.glob("*.pdf")):
        yield p


def cmd_parse(in_dir: Path, out_csv: Path) -> int:
    _load_dotenv_lazy()
    password = os.environ.get("PDF_PASSWORD", "").strip()
    if not password or password.upper() in ("YOUR_PAN_HERE", "YOUR_PAN"):
        print(
            "ERROR: PDF_PASSWORD env var is not set (expected your PAN).",
            file=sys.stderr,
        )
        return 1

    pdfs = list(_iter_pdfs(in_dir))
    if not pdfs:
        print(f"No PDFs found in {in_dir}")
        # still write an empty / unchanged CSV so downstream steps don't fail
        if not out_csv.exists():
            out_csv.parent.mkdir(parents=True, exist_ok=True)
            with out_csv.open("w", newline="") as f:
                csv.writer(f).writerow(CSV_HEADER)
        return 0

    print(f"Parsing {len(pdfs)} PDF(s) from {in_dir}")
    all_orders: list[Order] = []
    bad: list[str] = []
    for pdf in pdfs:
        try:
            orders = parse_pdf(pdf, password)
        except Exception as exc:
            bad.append(f"  ! {pdf.name}: {exc}")
            continue
        if not orders:
            bad.append(f"  ! {pdf.name}: no orders parsed (run --inspect to debug)")
            continue
        all_orders.extend(orders)

    print(f"  → {len(all_orders)} order leg(s) extracted")
    if bad:
        for b in bad[:10]:
            print(b)
        if len(bad) > 10:
            print(f"  ... and {len(bad) - 10} more")

    matched = fifo_match(all_orders)
    print(f"  → {len(matched)} matched round-trip trade(s) after FIFO")

    total, added = write_merged_csv(out_csv, matched)
    print(f"  → CSV {out_csv} now has {total} trade(s) ({added} new)")
    return 0


def cmd_inspect(pdf_path: Path) -> int:
    _load_dotenv_lazy()
    password = os.environ.get("PDF_PASSWORD", "").strip()
    if not password or password.upper() in ("YOUR_PAN_HERE", "YOUR_PAN"):
        print("ERROR: PDF_PASSWORD env var is not set.", file=sys.stderr)
        return 1
    if not pdf_path.exists():
        print(f"ERROR: {pdf_path} does not exist", file=sys.stderr)
        return 1

    text = extract_text(pdf_path, password)
    print(f"========== Raw text from {pdf_path.name} ==========")
    print(text)
    print("========== End raw text ==========\n")

    if _CONTRACT_NOTE_SYM_RE.search(text or ""):
        layout = "contract-note"
        orders = parse_contract_note_text(text, source_pdf=pdf_path.name)
    else:
        layout = "trade-details"
        orders = parse_orders_from_text(text, source_pdf=pdf_path.name)
    print(f"Detected layout: {layout}")
    print(f"Parsed {len(orders)} order(s):")
    for o in orders:
        print(
            f"  {o.timestamp:%Y-%m-%d %H:%M:%S}  {o.side:4}  {o.symbol:20}  "
            f"qty={o.qty}  px={o.price}  status={o.status}"
        )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Parse Dhan trade PDFs into TradesFromPDFs.csv")
    parser.add_argument("--in", dest="in_dir", default="Trades log",
                        help="Folder containing trade PDFs (default: 'Trades log')")
    parser.add_argument("--out", default="TradesFromPDFs.csv",
                        help="CSV output path (default: 'TradesFromPDFs.csv')")
    parser.add_argument("--inspect", metavar="PDF", default=None,
                        help="Print raw text from a single PDF (no CSV write)")
    args = parser.parse_args(argv)

    if args.inspect:
        return cmd_inspect(Path(args.inspect))
    return cmd_parse(Path(args.in_dir), Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
