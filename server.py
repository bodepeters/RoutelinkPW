from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = ROOT / "data"
UPLOADS = ROOT / "uploads"
DB_PATH = DATA / "pricewatch.db"
HOST = "127.0.0.1"
PORT = int(os.environ.get("PRICEWATCH_PORT", "8765"))

for folder in (STATIC, DATA, UPLOADS):
    folder.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS partners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_id INTEGER NOT NULL,
                baseline_file TEXT NOT NULL,
                current_file TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(partner_id) REFERENCES partners(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_id INTEGER,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(partner_id) REFERENCES partners(id) ON DELETE CASCADE
            );
            """
        )
        if db.execute("SELECT COUNT(*) FROM partners").fetchone()[0] == 0:
            cur = db.execute(
                "INSERT INTO partners(name,email,created_at) VALUES (?,?,?)",
                ("Acme Corp", "pricing@acme.com", now_iso()),
            )
            partner_id = cur.lastrowid
            db.execute(
                "INSERT INTO activity(partner_id,level,message,created_at) VALUES (?,?,?,?)",
                (partner_id, "ok", "Workspace initialized", now_iso()),
            )


def clean_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def parse_price(value: str):
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def parse_csv(raw: bytes, filename: str) -> list[dict]:
    if filename.lower().endswith((".xlsx", ".xls")):
        raise ValueError("Excel files are not enabled in this dependency-free MVP. Export the sheet as CSV and upload it.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError(f"{filename}: no header row found")
    fields = {clean_header(field): field for field in reader.fieldnames if field}
    sku_key = next((fields[k] for k in ("sku", "product_code", "item_code", "code", "id") if k in fields), None)
    product_key = next((fields[k] for k in ("product", "product_name", "name", "description") if k in fields), None)
    price_key = next((fields[k] for k in ("price", "unit_price", "amount", "rate", "cost") if k in fields), None)
    billing_key = next((fields[k] for k in ("billing_increment", "billing_period", "billing", "frequency", "term") if k in fields), None)
    if not sku_key and not product_key:
        raise ValueError(f"{filename}: expected an SKU or product column")
    if not price_key:
        raise ValueError(f"{filename}: expected a price, rate, amount, or cost column")
    rows = []
    for index, raw_row in enumerate(reader, start=2):
        sku = (raw_row.get(sku_key, "") if sku_key else "").strip()
        product = (raw_row.get(product_key, "") if product_key else "").strip()
        if not sku and not product:
            continue
        price = parse_price(raw_row.get(price_key, ""))
        if price is None:
            raise ValueError(f"{filename}: row {index} has no numeric price")
        rows.append({
            "sku": sku or product,
            "product": product or sku,
            "category": raw_row.get(fields.get("category", ""), "").strip() if fields.get("category") else "",
            "price": price,
            "unit": raw_row.get(fields.get("unit", ""), "").strip() if fields.get("unit") else "",
            "billing_increment": (raw_row.get(billing_key, "").strip() if billing_key else ""),
            "status": raw_row.get(fields.get("status", ""), "").strip() if fields.get("status") else "Active",
        })
    if not rows:
        raise ValueError(f"{filename}: no data rows found")
    return rows


def row_key(row):
    return row["sku"].strip().lower() or row["product"].strip().lower()


def diff_rows(baseline: list[dict], current: list[dict]) -> dict:
    before = {row_key(row): row for row in baseline}
    after = {row_key(row): row for row in current}
    changes = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old is None:
            changes.append({"type": "added", "severity": "normal", "sku": new["sku"], "product": new["product"], "new": new})
            continue
        if new is None:
            changes.append({"type": "removed", "severity": "high", "sku": old["sku"], "product": old["product"], "old": old})
            continue
        price_changed = round(old["price"], 8) != round(new["price"], 8)
        billing_changed = old["billing_increment"].strip().lower() != new["billing_increment"].strip().lower()
        product_changed = old["product"].strip().lower() != new["product"].strip().lower()
        if price_changed or billing_changed or product_changed:
            delta = round(new["price"] - old["price"], 8)
            pct = round((delta / old["price"]) * 100, 2) if old["price"] else None
            types = []
            if price_changed:
                types.append("price_up" if delta > 0 else "price_down")
            if billing_changed:
                types.append("billing")
            if product_changed:
                types.append("product")
            changes.append({
                "type": "+".join(types),
                "severity": "high" if delta > 0 or billing_changed else "normal",
                "sku": new["sku"], "product": new["product"],
                "old": old, "new": new, "delta": delta, "percent": pct,
            })
    summary = {
        "price_increases": sum(1 for c in changes if "price_up" in c["type"]),
        "price_decreases": sum(1 for c in changes if "price_down" in c["type"]),
        "billing_changes": sum(1 for c in changes if "billing" in c["type"]),
        "added": sum(1 for c in changes if c["type"] == "added"),
        "removed": sum(1 for c in changes if c["type"] == "removed"),
        "rows_baseline": len(baseline), "rows_current": len(current),
        "total_changes": len(changes),
    }
    return {"summary": summary, "changes": changes, "current_rows": current}


def save_upload(raw: bytes, original_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(original_name).name)
    stored = f"{uuid.uuid4().hex[:10]}_{safe}"
    (UPLOADS / stored).write_bytes(raw)
    return stored


def json_response(handler, payload, status=HTTPStatus.OK):
    data = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def parse_multipart(content_type: str, body: bytes):
    match = re.search(r"boundary=([^;]+)", content_type)
    if not match:
        raise ValueError("Missing multipart boundary")
    boundary = match.group(1).strip().strip('"').encode()
    parts = {}
    for part in body.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        header_blob, content = part.split(b"\r\n\r\n", 1)
        content = content.rstrip(b"\r\n-")
        headers = header_blob.decode("utf-8", "ignore")
        disposition = re.search(r' name="([^"]+)"(?:; filename="([^"]+)")?', headers)
        if disposition:
            name, filename = disposition.groups()
            parts[name] = {"filename": filename, "content": content}
    return parts


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")

    def send_file(self, path: Path, content_type: str):
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/index.html":
            return self.send_file(STATIC / "index.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            target = STATIC / Path(path[len("/static/"):]).name
            if target.exists():
                ctype = {".css": "text/css", ".js": "application/javascript", ".svg": "image/svg+xml"}.get(target.suffix, "application/octet-stream")
                return self.send_file(target, ctype)
        if path == "/api/partners":
            with get_db() as db:
                partners = [dict(row) for row in db.execute("SELECT * FROM partners ORDER BY name")]
                for partner in partners:
                    latest = db.execute("SELECT id, created_at, result_json FROM scans WHERE partner_id=? ORDER BY id DESC LIMIT 1", (partner["id"],)).fetchone()
                    partner["latest_scan"] = {"id": latest["id"], "created_at": latest["created_at"], "summary": json.loads(latest["result_json"])["summary"]} if latest else None
            return json_response(self, {"partners": partners})
        if path.startswith("/api/partners/") and path.endswith("/activity"):
            partner_id = path.split("/")[3]
            with get_db() as db:
                logs = [dict(row) for row in db.execute("SELECT * FROM activity WHERE partner_id=? ORDER BY id DESC LIMIT 50", (partner_id,))]
            return json_response(self, {"activity": logs})
        if path.startswith("/api/scans/"):
            scan_id = path.split("/")[3]
            with get_db() as db:
                row = db.execute("SELECT scans.*, partners.name AS partner_name, partners.email AS partner_email FROM scans JOIN partners ON partners.id=scans.partner_id WHERE scans.id=?", (scan_id,)).fetchone()
            if not row:
                return json_response(self, {"error": "Scan not found"}, HTTPStatus.NOT_FOUND)
            result = json.loads(row["result_json"])
            result.update({"id": row["id"], "partner_id": row["partner_id"], "partner_name": row["partner_name"], "partner_email": row["partner_email"], "baseline_file": row["baseline_file"], "current_file": row["current_file"], "created_at": row["created_at"]})
            return json_response(self, result)
        if path == "/api/health":
            return json_response(self, {"ok": True})
        self.send_error(HTTPStatus.NOT_FOUND)

    def read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/partners":
            try:
                payload = json.loads(self.read_body() or b"{}")
                name, email = payload.get("name", "").strip(), payload.get("email", "").strip()
                if not name:
                    raise ValueError("Partner name is required")
                with get_db() as db:
                    cur = db.execute("INSERT INTO partners(name,email,created_at) VALUES (?,?,?)", (name, email, now_iso()))
                    partner_id = cur.lastrowid
                    db.execute("INSERT INTO activity(partner_id,level,message,created_at) VALUES (?,?,?,?)", (partner_id, "ok", "Partner created", now_iso()))
                return json_response(self, {"id": partner_id, "name": name, "email": email}, HTTPStatus.CREATED)
            except Exception as exc:
                return json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if path.startswith("/api/partners/") and path.endswith("/scan"):
            partner_id = path.split("/")[3]
            try:
                content_type = self.headers.get("Content-Type", "")
                parts = parse_multipart(content_type, self.read_body())
                baseline_part, current_part = parts.get("baseline"), parts.get("current")
                if not baseline_part or not current_part:
                    raise ValueError("Upload both baseline and current CSV files")
                baseline = parse_csv(baseline_part["content"], baseline_part["filename"] or "baseline.csv")
                current = parse_csv(current_part["content"], current_part["filename"] or "current.csv")
                result = diff_rows(baseline, current)
                baseline_file = save_upload(baseline_part["content"], baseline_part["filename"] or "baseline.csv")
                current_file = save_upload(current_part["content"], current_part["filename"] or "current.csv")
                with get_db() as db:
                    partner = db.execute("SELECT * FROM partners WHERE id=?", (partner_id,)).fetchone()
                    if not partner:
                        raise ValueError("Partner not found")
                    cur = db.execute("INSERT INTO scans(partner_id,baseline_file,current_file,result_json,created_at) VALUES (?,?,?,?,?)", (partner_id, baseline_file, current_file, json.dumps(result), now_iso()))
                    scan_id = cur.lastrowid
                    summary = result["summary"]
                    level = "warn" if summary["total_changes"] else "ok"
                    msg = f"Scan complete: {summary['total_changes']} change(s) detected"
                    db.execute("INSERT INTO activity(partner_id,level,message,created_at) VALUES (?,?,?,?)", (partner_id, level, msg, now_iso()))
                result.update({"id": scan_id, "partner_id": int(partner_id), "partner_name": partner["name"], "partner_email": partner["email"], "baseline_file": baseline_file, "current_file": current_file, "created_at": now_iso()})
                return json_response(self, result, HTTPStatus.CREATED)
            except Exception as exc:
                return json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if path.startswith("/api/scans/") and path.endswith("/notify-preview"):
            scan_id = path.split("/")[3]
            with get_db() as db:
                row = db.execute("SELECT scans.*, partners.name, partners.email FROM scans JOIN partners ON partners.id=scans.partner_id WHERE scans.id=?", (scan_id,)).fetchone()
            if not row:
                return json_response(self, {"error": "Scan not found"}, HTTPStatus.NOT_FOUND)
            result = json.loads(row["result_json"])
            s = result["summary"]
            subject = f"PriceWatch Alert — {row['name']}: {s['total_changes']} Change(s) Detected"
            body = f"The latest pricing sheet for {row['name']} has {s['total_changes']} detected change(s): {s['price_increases']} price increase(s), {s['price_decreases']} decrease(s), and {s['billing_changes']} billing change(s).\n\nThis is a preview only; no email was sent."
            with get_db() as db:
                db.execute("INSERT INTO activity(partner_id,level,message,created_at) VALUES (?,?,?,?)", (row["partner_id"], "ok", "Notification preview generated (not sent)", now_iso()))
            return json_response(self, {"to": row["email"], "subject": subject, "body": body, "sent": False})
        self.send_error(HTTPStatus.NOT_FOUND)


if __name__ == "__main__":
    init_db()
    print(f"PriceWatch MVP running at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
