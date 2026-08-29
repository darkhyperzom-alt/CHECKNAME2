import os
import re
import asyncio
import threading
import time
import json as _json
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()  # โหลดค่าจากไฟล์ .env ถ้ามี (สำหรับรันที่เครื่องตัวเอง)

import psycopg2
import psycopg2.extras
import psycopg2.pool

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from flask import Flask, jsonify, send_from_directory, request, Response

# ----- ตั้งค่า -----
# ค่าทั้งหมดอ่านจาก environment variable เท่านั้น (ห้ามฝังค่าจริงในโค้ดที่จะขึ้น git)
api_id = int(os.environ.get("TG_API_ID", "0"))
api_hash = os.environ.get("TG_API_HASH", "")
session_string = os.environ.get("TG_SESSION", "")
group_id = int(os.environ.get("TG_GROUP_ID", "0"))
checkin_group_id = int(os.environ.get("TG_GROUP_ID_CHECKIN", "0"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# คนที่ไม่มีความเคลื่อนไหวเกินกี่วัน ให้หายไปจากแดชบอร์ดเอง (คนลาออก/ย้ายทีม)
# ข้อมูลเดิมยังอยู่ในฐานข้อมูล แค่ไม่แสดงผล — ปรับได้โดยไม่ต้องแก้โค้ด
ACTIVE_DAYS = int(os.environ.get("ACTIVE_DAYS", "14"))

# ล็อกอินเข้าแดชบอร์ด (ถ้าไม่ตั้ง DASH_USER จะเปิดให้เข้าฟรีเหมือนเดิม)
DASH_USER = os.environ.get("DASH_USER", "")
DASH_PASS = os.environ.get("DASH_PASS", "")

# เปิด CORS เฉพาะเมื่อระบุโดเมนไว้ (เว้นว่าง = ปิด ปลอดภัยกว่า)
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "")

DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "8"))
SHIFT_CACHE_TTL = 60  # วินาที — cache ตารางกะไว้ ไม่ต้อง query ทุกครั้ง
# รอบสูงสุดระหว่างการ refresh (วินาที) — ถ้ามีข้อความใหม่จาก Telegram จะ refresh ทันทีไม่ต้องรอครบ
SNAPSHOT_INTERVAL = float(os.environ.get("SNAPSHOT_INTERVAL", "2"))

# ปุ่มกิจกรรมที่บอทมี — เพิ่มปุ่มใหม่ได้โดยไม่ต้องแก้โค้ด
# ตั้ง env: ACTIVITIES=กินข้าว,ปวดหนัก,ปวดน้อย,พักเบรก
ACTIVITIES = [a.strip() for a in os.environ.get(
    "ACTIVITIES", "กินข้าว,ปวดหนัก,ปวดน้อย").split(",") if a.strip()]

# ในกลุ่มเช็คชื่อ: ถ้าตั้งเป็น 1 จะนับเฉพาะข้อความที่แนบรูปเท่านั้น
# (กันคนพิมพ์คุยเล่นในกลุ่มแล้วได้เช็คชื่อฟรี)
CHECKIN_REQUIRE_PHOTO = os.environ.get("CHECKIN_REQUIRE_PHOTO", "").strip().lower() in ("1", "true", "yes")
# ข้อความยาวเกินนี้ในกลุ่มเช็คชื่อ ถือว่าเป็นการคุย ไม่ใช่เช็คชื่อ
CHECKIN_MAX_LEN = int(os.environ.get("CHECKIN_MAX_LEN", "200"))

# ชื่อที่ลงท้ายด้วยคำพวกนี้ ไม่ใช่พนักงานของเรา ให้กรองออก
EXCLUDED_SUFFIXES = [
    "Vv72", "PG688", "Jun88", "MK8", "JL69",
    "F168", "K188", "NM9", "BT678", "TH26",
]

# บอทตัวนี้แปะบล็อก "⚠️ คำเตือน： ...เกินเวลา!" ไว้ใน "ข้อความที่สำเร็จจริง" ด้วย
# ฉะนั้นห้ามใช้คำว่า เตือน/เกินเวลา เป็นตัวตัดสินว่าไม่สำเร็จ เด็ดขาด
# เหลือไว้เฉพาะคำที่แปลว่า "ไม่สำเร็จ" จริงๆ เท่านั้น
NOISE_WORDS = [
    "ยังไม่", "ไม่สำเร็จ", "ล้มเหลว", "กรุณา", "โปรด", "อย่าลืม",
]

# สัญญาณว่าบอทยืนยันว่าทำรายการสำเร็จ (ใช้ตอนข้อความไม่มีบรรทัด "ลงทะเบียนสำเร็จ")
SUCCESS_MARKERS = ["สำเร็จ", "✅", "回座", "成功", "เรียบร้อย"]

# บรรทัดสอนวิธีใช้ที่บอทแนบท้าย ไม่ใช่การกดจริง
INSTRUCTION_HINTS = [
    "/back", "ทิป", "คำแนะนำ", "คุณสามารถ", "หลังจาก", "เวลาจำกัด", "วิธีใช้", "กดปุ่ม", "คำสั่ง",
]

# ตั้ง DEBUG_RAW=1 เพื่อให้ log ข้อความดิบของทุกกลุ่ม (ใช้ตอนหาสาเหตุ แล้วปิดกลับ)
DEBUG_RAW = os.environ.get("DEBUG_RAW", "").strip().lower() not in ("", "0", "false", "no")

ROUND_LABELS = [
    "กะเช้า(08.00-20.00 น.) รอบที่ 1",
    "กะเช้า(08.00-20.00 น.) รอบที่ 2",
    "กะเช้า(08.00-20.00 น.) รอบที่ 3",
    "กะดึก(20.00-08.00 น.) รอบที่ 1",
    "กะดึก(20.00-08.00 น.) รอบที่ 2",
    "กะดึก(20.00-08.00 น.) รอบที่ 3",
]
# -------------------

if not api_id or not api_hash or not group_id:
    raise RuntimeError(
        "กรุณาตั้งค่า environment variable: TG_API_ID, TG_API_HASH, TG_GROUP_ID ก่อนรัน "
        "(ดูวิธีตั้งค่าใน README.md)"
    )

if not DATABASE_URL:
    raise RuntimeError(
        "กรุณาตั้งค่า environment variable: DATABASE_URL (connection string ของ PostgreSQL)"
    )

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ถ้ามี TG_SESSION (รันบน server) ใช้ string session, ถ้าไม่มี (รันที่คอมตัวเอง) ใช้ไฟล์ปกติ
_client_kwargs = dict(
    connection_retries=None,   # ลองใหม่ไม่จำกัด
    retry_delay=1,
    auto_reconnect=True,
    request_retries=5,
)
if session_string:
    client = TelegramClient(StringSession(session_string), api_id, api_hash, **_client_kwargs)
else:
    client = TelegramClient("my_session", api_id, api_hash, **_client_kwargs)

app = Flask(__name__)

if CORS_ORIGINS:
    from flask_cors import CORS
    CORS(app, origins=[o.strip() for o in CORS_ORIGINS.split(",") if o.strip()])


@app.before_request
def _require_auth():
    """ล็อกอินง่ายๆ แบบ Basic Auth — ทำงานเฉพาะเมื่อตั้ง DASH_USER/DASH_PASS ไว้เท่านั้น"""
    if not DASH_USER:
        return None
    auth = request.authorization
    if auth and auth.username == DASH_USER and auth.password == DASH_PASS:
        return None
    return Response(
        "ต้องล็อกอินก่อนใช้งาน", 401,
        {"WWW-Authenticate": 'Basic realm="dashboard"'},
    )


# ===== ส่วนฐานข้อมูล (PostgreSQL) =====

# เงื่อนไข SQL กรองชื่อที่ลงท้ายด้วย suffix ที่ไม่ใช่พนักงานของเรา (ใช้ซ้ำหลายจุด)
EXCLUDE_SQL = " AND " + " AND ".join("username NOT LIKE %s" for _ in EXCLUDED_SUFFIXES)
EXCLUDE_PARAMS = tuple(f"%{suf}" for suf in EXCLUDED_SUFFIXES)

_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    """connection pool — เดิมเปิด connection ใหม่ทุก query (นาทีละหลายร้อยครั้ง)"""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    1, DB_POOL_MAX,
                    dsn=DATABASE_URL,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
    return _pool


@contextmanager
def db(commit=False):
    """ยืม connection จาก pool และคืนให้เสมอ แม้ query จะพัง (เดิมมี connection รั่ว)"""
    pool = _get_pool()
    conn = pool.getconn()

    # เช็คว่า connection ยังใช้ได้จริง (กันเคสค้างจาก DB restart / เน็ตหลุด)
    try:
        if conn.closed:
            raise psycopg2.OperationalError("connection closed")
        with conn.cursor() as ping:
            ping.execute("SELECT 1")
        conn.rollback()
    except psycopg2.Error:
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass
        conn = pool.getconn()

    cur = conn.cursor()
    try:
        yield cur
        if commit:
            conn.commit()
        else:
            conn.rollback()  # ปิด transaction ของ SELECT ไม่ให้ค้างเป็น idle in transaction
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
        pool.putconn(conn)


def norm_name(name):
    """ทำชื่อให้เทียบกันได้ — ตัดช่องว่างหัวท้าย/ซ้ำ และไม่สนตัวพิมพ์เล็กใหญ่
    (กันเคส 'ODOL-TAO-HT99 🎲' หรือ iOS123 / IOS123 หากันไม่เจอ)"""
    return re.sub(r"\s+", " ", (name or "")).strip().lower()


_shift_cache = {"data": {}, "at": 0.0, "loaded": False}
_shift_lock = threading.Lock()


def load_shift_map(force=False):
    """โหลดรายชื่อกะ (ชื่อ normalize แล้ว -> 'เช้า'/'ดึก') จาก PostgreSQL พร้อม cache"""
    now_ts = time.time()
    with _shift_lock:
        if not force and _shift_cache["loaded"] and now_ts - _shift_cache["at"] < SHIFT_CACHE_TTL:
            return _shift_cache["data"]
    try:
        with db() as cur:
            cur.execute("SELECT username, shift FROM shift_assignments")
            result = {norm_name(r["username"]): r["shift"] for r in cur.fetchall()}
        with _shift_lock:
            _shift_cache["data"] = result
            _shift_cache["at"] = now_ts
            _shift_cache["loaded"] = True
        return result
    except Exception as e:
        print(f"โหลดข้อมูลกะไม่สำเร็จ: {e}")
        with _shift_lock:
            return _shift_cache["data"]


def invalidate_shift_cache():
    with _shift_lock:
        _shift_cache["at"] = 0.0


def init_db():
    with db(commit=True) as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS current_status (
                user_id TEXT PRIMARY KEY,
                username TEXT,
                status TEXT,
                since TIMESTAMPTZ
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS status_log (
                id SERIAL PRIMARY KEY,
                user_id TEXT,
                username TEXT,
                activity TEXT,
                timestamp TIMESTAMPTZ,
                raw_text TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS round_status (
                round_label TEXT PRIMARY KEY,
                announced_at TIMESTAMPTZ
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS checkin_log (
                user_id TEXT,
                round_label TEXT,
                checked_at TIMESTAMPTZ,
                PRIMARY KEY (user_id, round_label)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS shift_assignments (
                username TEXT PRIMARY KEY,
                shift TEXT
            )
        """)
        # เพิ่ม index เพื่อให้ query เร็วขึ้น (สำคัญมากเมื่อข้อมูลสะสมเยอะ)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_status_log_ts ON status_log (timestamp)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_status_log_user_ts ON status_log (user_id, timestamp)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_status_log_username ON status_log (username)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_current_status_since ON current_status (since)")


def get_period_start(now, override=None):
    """หาจุดเริ่มรอบกะปัจจุบัน (รีเซตทุก 08:05 และ 20:05 'เวลาไทย' เสมอ
    ไม่ว่า server จะรันอยู่ timezone ไหนก็ตาม) — now ต้องเป็น UTC-aware datetime
    ถ้าระบุ override ('เช้า'/'ดึก') จะคืนจุดเริ่มรอบล่าสุดของกะนั้นแทนกะตามเวลาจริง
    (ใช้ตอนผู้ใช้กดดูกะอื่นที่ไม่ตรงกับเวลาจริง — เดิมไม่รองรับ ทำให้รอบที่ประกาศไปแล้วจริง
    ของกะที่ไม่ใช่กะปัจจุบันขึ้นเป็น "ยังไม่ประกาศ" ทันทีที่ผ่านจุดตัดรอบของเวลาจริง)"""
    now_bkk = now.astimezone(BANGKOK_TZ)

    bkk_0805 = now_bkk.replace(hour=8, minute=5, second=0, microsecond=0)
    bkk_2005 = now_bkk.replace(hour=20, minute=5, second=0, microsecond=0)

    if override == "เช้า":
        period_start_bkk = bkk_0805 if now_bkk >= bkk_0805 else bkk_0805 - timedelta(days=1)
        return period_start_bkk.astimezone(timezone.utc)
    if override == "ดึก":
        period_start_bkk = bkk_2005 if now_bkk >= bkk_2005 else bkk_2005 - timedelta(days=1)
        return period_start_bkk.astimezone(timezone.utc)

    if now_bkk >= bkk_2005:
        period_start_bkk = bkk_2005
    elif now_bkk >= bkk_0805:
        period_start_bkk = bkk_0805
    else:
        period_start_bkk = bkk_2005 - timedelta(days=1)

    return period_start_bkk.astimezone(timezone.utc)


def get_current_shift_rounds(now, override=None):
    """คืนชื่อ 3 รอบของกะ — ถ้าระบุ override ('เช้า'/'ดึก') จะใช้กะนั้นแทนเวลาจริง"""
    if override == "เช้า":
        return ROUND_LABELS[0], ROUND_LABELS[1], ROUND_LABELS[2]
    if override == "ดึก":
        return ROUND_LABELS[3], ROUND_LABELS[4], ROUND_LABELS[5]

    now_bkk = now.astimezone(BANGKOK_TZ)
    is_day_shift = (now_bkk.hour, now_bkk.minute) >= (8, 5) and (now_bkk.hour, now_bkk.minute) < (20, 5)
    if is_day_shift:
        return ROUND_LABELS[0], ROUND_LABELS[1], ROUND_LABELS[2]
    else:
        return ROUND_LABELS[3], ROUND_LABELS[4], ROUND_LABELS[5]


def save_round_announcement(round_label, now):
    with db(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO round_status (round_label, announced_at)
            VALUES (%s, %s)
            ON CONFLICT (round_label) DO UPDATE SET announced_at = EXCLUDED.announced_at
            """,
            (round_label, now),
        )


def save_checkin(user_id, now):
    """บันทึกว่า user_id เช็คชื่อสำหรับรอบล่าสุดที่ถูกประกาศ 'ในกะปัจจุบัน'
    (เดิมไม่กรองกะ ทำให้ไปเกาะรอบของเมื่อวานแล้วเงียบหาย)"""
    period_start = get_period_start(now)
    with db(commit=True) as cur:
        cur.execute(
            """
            SELECT round_label FROM round_status
            WHERE announced_at >= %s
            ORDER BY announced_at DESC LIMIT 1
            """,
            (period_start,),
        )
        row = cur.fetchone()
        if not row:
            print("   (ยังไม่มีรอบไหนถูกประกาศในกะนี้ — ข้ามการบันทึกเช็คชื่อ)")
            return

        current_round = row["round_label"]
        cur.execute(
            """
            INSERT INTO checkin_log (user_id, round_label, checked_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, round_label) DO UPDATE SET checked_at = EXCLUDED.checked_at
            """,
            (user_id, current_round, now),
        )


# ===== ส่วน parse ข้อความ Telegram =====

def clean_text(text):
    return text.replace("**", "").replace("`", "")


def classify_result(result_text):
    """แปลงข้อความผลลัพธ์ที่บอทยืนยัน ให้เป็นชื่อกิจกรรมที่เรารู้จัก"""
    if "กลับที่นั่ง" in result_text:
        return "กลับที่นั่ง"
    for act in ACTIVITIES:
        if act in result_text:
            return act
    return None


def parse_message(text):
    """อ่านข้อความบอทแบบ "ทีละบรรทัด" ไม่ใช่หาคำทั้งก้อน

    เหตุผล: ข้อความยืนยันตอนกดออกไปไหน จะมีคำว่า "กลับที่นั่ง" แถมมาเสมอ
    ในบรรทัดสอนวิธีใช้ (ทิป／/back) ถ้าหาคำแบบทั้งก้อนจะอ่านสลับกันทันที
    """
    cleaned = clean_text(text)

    # (?<!รหัส) กัน regex ไปแมตช์บรรทัด "รหัสผู้ใช้" แทนบรรทัด "ผู้ใช้"
    user_match = re.search(r"(?<!รหัส)ผู้ใช้\s*[:：]\s*\[?([^\]\(\n]+)", cleaned)
    userid_match = re.search(r"รหัสผู้ใช้\s*[:：]\s*(\d+)", cleaned)

    if not user_match or not userid_match:
        return None

    username = user_match.group(1).strip()
    user_id = userid_match.group(1).strip()

    if "ODOL" not in username:
        return None  # ไม่ใช่ ODOL ข้ามไปเลย

    if any(username.endswith(suf) for suf in EXCLUDED_SUFFIXES):
        return None  # ลงท้ายด้วย suffix ที่ไม่ใช่พนักงานของเรา ข้ามไปเลย

    lines = cleaned.split("\n")

    # --- ทางหลัก: ยึดจากบรรทัด "ลงทะเบียนสำเร็จ： xxx" เท่านั้น ---
    for line in lines:
        if "ลงทะเบียนสำเร็จ" not in line:
            continue
        # บอทเขียนได้ 2 แบบ ต้องรับได้ทั้งคู่:
        #   ออกไป : "✅ ลงทะเบียนสำเร็จ： ปวดน้อย.สูบบุหรี่ - 07/29 21:07:31"
        #   กลับมา: "✅ 07/29 23:11:46 ลงทะเบียนสำเร็จ กลับที่นั่ง สำเร็จ : ปวดหนัก"
        # จึงตัดทุกอย่างหน้าคำว่า "ลงทะเบียนสำเร็จ" ทิ้ง แล้วดูเฉพาะส่วนที่เหลือ
        result_text = line.split("ลงทะเบียนสำเร็จ", 1)[1].lstrip(" \t:：")
        # ตัดวันที่เวลาท้ายบรรทัดทิ้ง เช่น "ปวดน้อย.สูบบุหรี่ - 07/29 21:07:31"
        result_text = re.split(r"\s+-\s+\d", result_text)[0].strip()

        activity = classify_result(result_text)
        if activity:
            return {"user_id": user_id, "username": username,
                    "activity": activity, "detail": result_text, "raw": cleaned}

        # มีบรรทัดยืนยันแต่ไม่รู้จักกิจกรรม — อย่าเดา ให้ log ไว้แล้วข้าม
        print(f"[ไม่รู้จักกิจกรรม] {username}: {result_text!r}")
        return None

    # --- ทางสำรอง: ข้อความ "กลับที่นั่ง" ที่ไม่มีบรรทัดลงทะเบียนสำเร็จ ---
    if "กลับที่นั่ง" in cleaned:
        ok = any(m in cleaned for m in SUCCESS_MARKERS)
        bad = next((w for w in NOISE_WORDS if w in cleaned), None)

        if ok and not bad:
            back_lines = [l for l in lines if "กลับที่นั่ง" in l]
            if not any(not any(h in l for h in INSTRUCTION_HINTS) for l in back_lines):
                print(f"[น่าสงสัย] {username}: เจอ 'กลับที่นั่ง' เฉพาะในบรรทัดสอนวิธีใช้ "
                      f"— นับเป็นกลับที่นั่งไว้ก่อน: {cleaned[:80]!r}")
            return {"user_id": user_id, "username": username,
                    "activity": "กลับที่นั่ง", "detail": "กลับที่นั่ง", "raw": cleaned}

        reason = f"เจอคำว่า '{bad}'" if bad else "ไม่มีสัญญาณยืนยันความสำเร็จ"
        print(f"[ข้าม] {username}: {reason} — ไม่นับเป็นการกดกลับ: {cleaned[:80]!r}")
        return None

    return None


def save_status(data, when=None):
    # ใช้เวลาที่ Telegram ประทับบนข้อความ ไม่ใช่เวลาที่เราประมวลผลเสร็จ
    now = when or datetime.now(timezone.utc)
    with db(commit=True) as cur:
        cur.execute(
            "INSERT INTO status_log (user_id, username, activity, timestamp, raw_text) VALUES (%s, %s, %s, %s, %s)",
            (data["user_id"], data["username"], data["activity"], now, data["raw"]),
        )
        cur.execute(
            """
            INSERT INTO current_status (user_id, username, status, since)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                status = EXCLUDED.status,
                since = EXCLUDED.since
            """,
            (data["user_id"], data["username"], data["activity"], now),
        )


CHAT_IDS = [group_id] + ([checkin_group_id] if checkin_group_id else [])


def event_time(event):
    """คืน (เวลาที่ Telegram ประทับบนข้อความ, ช้ากี่วินาทีกว่าจะถึงเรา)

    ถ้าอัปเดตมาถึงช้า ตัวเลขบนแดชบอร์ดจะยังตรงกับเวลาที่พนักงานกดจริง
    และค่า lag ใช้เป็นตัวชี้ว่าอาการช้าเกิดที่ฝั่ง Telegram หรือฝั่งเรา"""
    now = datetime.now(timezone.utc)
    when = getattr(event.message, "date", None)
    if not when:
        return now, 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    lag = (now - when).total_seconds()
    # กันเวลาเพี้ยน (นาฬิกาเครื่องไม่ตรง หรือ forward ข้อความเก่ามา)
    if lag < -60 or lag > 86400:
        return now, 0.0
    return when, lag


async def checkin_skip_reason(event, text):
    """คืนเหตุผลถ้าข้อความนี้ไม่ควรถูกนับเป็นการเช็คชื่อ, คืน None ถ้านับได้"""
    has_photo = bool(event.message.photo or event.message.media)

    if CHECKIN_REQUIRE_PHOTO and not has_photo:
        return "ตั้งค่าให้รับเฉพาะรูป"
    if text.startswith("/"):
        return "เป็นคำสั่ง"
    if not has_photo and len(text) > CHECKIN_MAX_LEN:
        return f"ข้อความยาวเกิน {CHECKIN_MAX_LEN} ตัว น่าจะเป็นการคุย"

    try:
        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return "ส่งโดยบอท"
    except Exception:
        pass
    return None


@client.on(events.NewMessage(chats=CHAT_IDS))
async def handler(event):
    await process_event(event)


@client.on(events.MessageEdited(chats=CHAT_IDS))
async def edited_handler(event):
    # บางบอทประกาศรอบ (เช่น OA-IVY-ONLINE) แก้ไข (edit) ข้อความเดิมแทนที่จะส่งข้อความใหม่
    # NewMessage ไม่ trigger ให้กับข้อความที่ถูกแก้ไข ต้องดัก MessageEdited แยกด้วย
    # ไม่งั้นการประกาศรอบจะเงียบหายไปทั้งที่ข้อความจริงเปลี่ยนแล้วในกลุ่ม
    await process_event(event)


async def process_event(event):
    # raw_text = ข้อความล้วน ไม่มีสัญลักษณ์ markdown (** __ ` ) ปนมา
    # ถ้าใช้ .text บอทที่ใส่ตัวหนา/ขีดเส้นใต้จะทำให้ regex หาไม่เจอ
    text = event.message.raw_text or event.message.text or ""
    when, lag = event_time(event)
    stamp = when.astimezone(BANGKOK_TZ).strftime("%H:%M:%S")
    lag_note = f"  [ข้อความมาถึงช้า {lag:.1f} วิ]" if lag > 3 else ""

    if event.chat_id == group_id:
        if not text:
            return
        data = parse_message(text)
        if data:
            # เขียน DB ใน thread แยก ไม่ให้ไปบล็อก event loop ของ Telethon
            t0 = time.monotonic()
            await asyncio.to_thread(save_status, data, when)
            db_ms = (time.monotonic() - t0) * 1000
            request_refresh()  # ดันขึ้นหน้าเว็บทันที ไม่ต้องรอรอบถัดไป
            print(f"[{stamp}] {data['username']} -> {data['activity']}"
                  f"  (เขียน DB {db_ms:.0f} ms){lag_note}")

    elif checkin_group_id and event.chat_id == checkin_group_id:
        # ตรวจจับประกาศรอบจาก "ข้อความล้วน" เท่านั้น (text ไม่ใช่ caption)
        # กัน forward รูปประกาศพร้อมแคปชั่นทำให้ announced_at ถูก reset ผิดพลาด
        round_label = None
        for label in ROUND_LABELS:
            if label in text:
                round_label = label
                break

        if round_label:
            await asyncio.to_thread(save_round_announcement, round_label, when)
            request_refresh()
            print(f"[{stamp}] ประกาศรอบใหม่: {round_label}{lag_note}")
        else:
            # เช็คชื่อ — รองรับทั้งส่งรูปเฉยๆ, ข้อความล้วน, หรือรูปพร้อมแคปชั่น
            # แต่กันข้อความที่เห็นชัดว่าไม่ใช่การเช็คชื่อออกก่อน
            skip = await checkin_skip_reason(event, text)
            if skip:
                print(f"[{stamp}] ข้ามในกลุ่มเช็คชื่อ ({skip})")
                return
            sender_id = str(event.sender_id)
            await asyncio.to_thread(save_checkin, sender_id, when)
            request_refresh()
            print(f"[{stamp}] เช็คชื่อจาก user_id {sender_id}{lag_note}")


if DEBUG_RAW:
    @client.on(events.NewMessage())
    async def debug_handler(event):
        """log ข้อความดิบจากทุกกลุ่ม เพื่อดูว่า chat_id ตรงกับที่ตั้งไว้ไหม
        และข้อความจริงหน้าตาเป็นยังไง — เปิดด้วย DEBUG_RAW=1 แล้วปิดกลับเมื่อเสร็จ"""
        try:
            raw = event.message.raw_text or ""
            if not raw:
                return
            which = []
            if event.chat_id == group_id:
                which.append("= TG_GROUP_ID")
            if checkin_group_id and event.chat_id == checkin_group_id:
                which.append("= TG_GROUP_ID_CHECKIN")
            tag = " ".join(which) or "ไม่ได้ตั้งค่าไว้"
            print("\n" + "=" * 60)
            print(f"[DEBUG] chat_id={event.chat_id}  ({tag})")
            print("-" * 60)
            print(raw[:1200])
            print("-" * 60)
            print(f"[DEBUG] ผล parse: {parse_message(raw)}")
            print("=" * 60 + "\n")
        except Exception as e:
            print(f"debug_handler error: {e}")


# ===== ส่วน Flask API =====

@app.route("/")
def serve_dashboard():
    return send_from_directory(BASE_DIR, "dashboard.html")


@app.route("/shifts")
def serve_shift_editor():
    return send_from_directory(BASE_DIR, "shift_editor.html")


@app.route("/api/shift-assignments", methods=["GET"])
def get_shift_assignments():
    active_since = datetime.now(timezone.utc) - timedelta(days=ACTIVE_DAYS)
    with db() as cur:
        cur.execute("SELECT username, shift FROM shift_assignments ORDER BY username")
        assignments = [{"username": r["username"], "shift": r["shift"]} for r in cur.fetchall()]

        # รายชื่อที่ระบบเห็นความเคลื่อนไหวล่าสุด มาเป็นตัวช่วยเลือก กันพิมพ์/อิโมจิไม่ตรง
        cur.execute(
            "SELECT DISTINCT username FROM current_status "
            "WHERE username LIKE %s AND since >= %s ORDER BY username",
            ("%ODOL%", active_since),
        )
        known_usernames = [r["username"] for r in cur.fetchall()]

    return jsonify({"assignments": assignments, "known_usernames": known_usernames})


@app.route("/api/shift-assignments", methods=["POST"])
def upsert_shift_assignment():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    shift = (data.get("shift") or "").strip()

    if not username or shift not in ("เช้า", "ดึก"):
        return jsonify({"error": "ต้องระบุ username และ shift เป็น 'เช้า' หรือ 'ดึก' เท่านั้น"}), 400

    with db(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO shift_assignments (username, shift)
            VALUES (%s, %s)
            ON CONFLICT (username) DO UPDATE SET shift = EXCLUDED.shift
            """,
            (username, shift),
        )
    invalidate_shift_cache()
    return jsonify({"ok": True})


@app.route("/api/shift-assignments/<path:username>", methods=["DELETE"])
def delete_shift_assignment(username):
    with db(commit=True) as cur:
        cur.execute("DELETE FROM shift_assignments WHERE username = %s", (username,))
    invalidate_shift_cache()
    return jsonify({"ok": True})


@app.route("/api/manual-checkin", methods=["POST"])
def manual_checkin():
    data = request.get_json(silent=True) or {}
    user_id = str(data.get("user_id") or "").strip()
    round_num = data.get("round")  # 1, 2 หรือ 3
    shift_override = data.get("shift")
    if shift_override not in ("เช้า", "ดึก"):
        shift_override = None

    if not user_id or round_num not in (1, 2, 3):
        return jsonify({"error": "ต้องระบุ user_id และ round (1, 2 หรือ 3)"}), 400

    now = datetime.now(timezone.utc)
    # เดิมไม่รับค่ากะที่หน้าจอเลือกอยู่ ทำให้กดในโหมด "เช้า" ตอนกลางคืนแล้วไปลงรอบดึก
    round1_label, round2_label, round3_label = get_current_shift_rounds(now, override=shift_override)
    target_label = {1: round1_label, 2: round2_label, 3: round3_label}[round_num]
    # period_start ต้องยึดตามกะที่กำลังดูอยู่ด้วย ไม่งั้นเช็คว่า announced_at เก่ากว่ากะปัจจุบันผิด
    # แล้วไปเขียนทับเวลาประกาศของกะที่ไม่ใช่กะปัจจุบันจริง จนคนละกะเช็คชื่อจริงมาเกาะรอบผิด
    period_start = get_period_start(now, override=shift_override)

    with db(commit=True) as cur:
        # บังคับลำดับ 1 -> 2 -> 3 เท่านั้น — รอบหลังจะเช็คชื่อ (มือหรืออัตโนมัติ) ไม่ได้
        # จนกว่ารอบก่อนหน้าจะถูกประกาศจริงในกะนี้ก่อน ไม่ว่าผู้ประกาศ (เช่น CHECKACCBOT) จะส่งข้อความสลับลำดับมาก็ตาม
        prior_labels = [round1_label, round2_label, round3_label][: round_num - 1]
        if prior_labels:
            cur.execute(
                "SELECT round_label, announced_at FROM round_status WHERE round_label = ANY(%s)",
                (prior_labels,),
            )
            announced = {r["round_label"]: r["announced_at"] for r in cur.fetchall()}
            missing = [lbl for lbl in prior_labels if not announced.get(lbl) or announced[lbl] < period_start]
            if missing:
                return jsonify({"error": f"ยังไม่ได้ประกาศรอบก่อนหน้า ({', '.join(missing)}) เช็คชื่อรอบนี้ก่อนไม่ได้"}), 400

        # ป้ายรอบใช้ซ้ำทุกวัน แถวเดิมจากเมื่อวานจึงมีอยู่เสมอ
        # ถ้า announced_at เก่ากว่ากะปัจจุบัน ต้องอัปเดตเป็นตอนนี้ ไม่งั้นกดแล้วไม่ขึ้นสีเขียว
        cur.execute("SELECT announced_at FROM round_status WHERE round_label = %s", (target_label,))
        row = cur.fetchone()
        if not row or not row["announced_at"] or row["announced_at"] < period_start:
            cur.execute(
                """
                INSERT INTO round_status (round_label, announced_at)
                VALUES (%s, %s)
                ON CONFLICT (round_label) DO UPDATE SET announced_at = EXCLUDED.announced_at
                """,
                (target_label, now),
            )

        cur.execute(
            """
            INSERT INTO checkin_log (user_id, round_label, checked_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, round_label) DO UPDATE SET checked_at = EXCLUDED.checked_at
            """,
            (user_id, target_label, now),
        )
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    shift_override = request.args.get("shift")
    if shift_override not in ("เช้า", "ดึก"):
        shift_override = None
    return jsonify(build_status_payload(shift_override))


def build_status_payload(shift_override=None, cur=None):
    """ถ้าส่ง cur เข้ามา จะใช้ connection เดิม (snapshot thread ใช้ตัวเดียวสร้างทั้ง 3 กะ)"""
    if cur is not None:
        return _build_status_payload(cur, shift_override)
    with db() as c:
        return _build_status_payload(c, shift_override)


def _build_status_payload(cur, shift_override=None):
    now = datetime.now(timezone.utc)
    # ต้องยึดกะที่กำลังดูอยู่ (shift_override) ไม่ใช่เวลาจริงเสมอ ไม่งั้นกดดูกะอื่นแล้วรอบที่ประกาศไปแล้วจริง
    # ของกะนั้นจะหายไปกลายเป็น "ยังไม่ประกาศ" ทันทีที่ผ่านจุดตัดรอบของเวลาจริง
    period_start = get_period_start(now, override=shift_override)
    active_since = now - timedelta(days=ACTIVE_DAYS)

    # แสดงเฉพาะคนที่ยังมีความเคลื่อนไหวภายใน ACTIVE_DAYS วัน
    # คนลาออกจะหลุดออกจากจอเองโดยไม่ต้องมาไล่ลบทีละคน
    cur.execute(
        "SELECT user_id, username, status, since FROM current_status "
        "WHERE username LIKE %s AND since >= %s" + EXCLUDE_SQL,
        ("%ODOL%", active_since) + EXCLUDE_PARAMS,
    )
    rows = cur.fetchall()

    # เวลารวมที่กดกิจกรรมทั้งหมดในรอบกะนี้ ต่อคน
    cur.execute(
        """
        SELECT user_id, activity, timestamp FROM status_log
        WHERE timestamp >= %s AND username LIKE %s
        """ + EXCLUDE_SQL + """
        ORDER BY user_id, timestamp ASC
        """,
        (period_start, "%ODOL%") + EXCLUDE_PARAMS,
    )
    log_rows = cur.fetchall()

    # กิจกรรมที่เริ่มก่อนต้นรอบแต่ยังไม่กดกลับที่นั่ง (ออกอยู่ตั้งแต่ก่อนรอบนี้เริ่ม)
    # เดิมไม่เช็คตรงนี้ ทำให้คนที่ออกไปก่อนรอบเปลี่ยนแล้วยังไม่กลับ นับเวลารวมของรอบนี้เป็น 0
    # ทั้งที่ duration_seconds (เวลาที่ออกอยู่ตอนนี้) นับถูกอยู่แล้ว
    cur.execute(
        """
        SELECT DISTINCT ON (user_id) user_id, activity FROM status_log
        WHERE timestamp < %s AND username LIKE %s
        """ + EXCLUDE_SQL + """
        ORDER BY user_id, timestamp DESC
        """,
        (period_start, "%ODOL%") + EXCLUDE_PARAMS,
    )
    carried_over = {r["user_id"] for r in cur.fetchall() if r["activity"] != "กลับที่นั่ง"}

    user_logs = defaultdict(list)
    for r in log_rows:
        user_logs[r["user_id"]].append(r)

    total_today_map = {}
    for uid in set(user_logs) | carried_over:
        log_list = user_logs.get(uid, [])
        total = 0.0
        # ถ้าออกมาตั้งแต่ก่อนต้นรอบ นับเวลาเริ่มจากต้นรอบ ไม่ใช่จากตอนที่ออกจริง (นั่นเป็นของรอบก่อนหน้า)
        out_since = period_start if uid in carried_over else None
        for row in log_list:
            if row["activity"] == "กลับที่นั่ง":
                if out_since is not None:
                    total += (row["timestamp"] - out_since).total_seconds()
                out_since = None
                continue
            if out_since is not None:
                total += (row["timestamp"] - out_since).total_seconds()
            out_since = row["timestamp"]
        if out_since is not None:
            total += (now - out_since).total_seconds()
        total_today_map[uid] = int(total)

    people = []
    for row in rows:
        since = row["since"]
        duration_seconds = int((now - since).total_seconds())
        people.append({
            "user_id": row["user_id"],
            "username": row["username"],
            "status": row["status"],
            "since": since.isoformat(),
            "duration_seconds": duration_seconds,
            "total_today_seconds": total_today_map.get(row["user_id"], 0),
        })

    cur.execute(
        """
        SELECT activity, COUNT(*) as count FROM status_log
        WHERE timestamp >= %s AND activity != 'กลับที่นั่ง' AND username LIKE %s
        """ + EXCLUDE_SQL + """
        GROUP BY activity
        """,
        (period_start, "%ODOL%") + EXCLUDE_PARAMS,
    )
    activity_counts = {r["activity"]: r["count"] for r in cur.fetchall()}
    out_count = sum(1 for p in people if p["status"] != "กลับที่นั่ง")

    # ===== ข้อมูลเช็คชื่อ (รอบที่ 1 / รอบที่ 2 / รอบที่ 3 ของกะปัจจุบัน) =====
    round1_label, round2_label, round3_label = get_current_shift_rounds(now, override=shift_override)

    cur.execute(
        "SELECT round_label, announced_at FROM round_status WHERE round_label IN (%s, %s, %s)",
        (round1_label, round2_label, round3_label),
    )
    # ตัดประกาศเก่าที่มาจากก่อนรอบกะปัจจุบันออก (เช่น ประกาศของเมื่อวาน) ถือว่ายังไม่ได้ประกาศในรอบนี้
    announced_map = {
        r["round_label"]: r["announced_at"]
        for r in cur.fetchall()
        if r["announced_at"] and r["announced_at"] >= period_start
    }

    # บังคับลำดับ 1 -> 2 -> 3 เท่านั้น — ต่อให้ผู้ประกาศ (เช่น CHECKACCBOT) ส่งข้อความรอบหลังมาก่อน
    # ก็ให้ระบบเราถือว่ารอบนั้น "ยังไม่ประกาศ" (เทา) จนกว่ารอบก่อนหน้าจะมาครบก่อน
    if round1_label not in announced_map:
        announced_map.pop(round2_label, None)
        announced_map.pop(round3_label, None)
    elif round2_label not in announced_map:
        announced_map.pop(round3_label, None)

    user_ids = [p["user_id"] for p in people]
    checkin_map = {}
    if user_ids:
        cur.execute(
            """
            SELECT user_id, round_label, checked_at FROM checkin_log
            WHERE round_label IN (%s, %s, %s) AND user_id = ANY(%s)
            """,
            (round1_label, round2_label, round3_label, user_ids),
        )
        for r in cur.fetchall():
            checkin_map[(r["user_id"], r["round_label"])] = r["checked_at"]

    def round_status_for(user_id, round_label, is_round1=False):
        announced_at = announced_map.get(round_label)
        checked_at = checkin_map.get((user_id, round_label))

        if not announced_at:
            return "gray", None
        if checked_at and checked_at >= announced_at:
            return "green", checked_at.astimezone(BANGKOK_TZ).strftime("%H:%M")
        if is_round1 and (now - announced_at) > timedelta(hours=1):
            return "holiday", None
        return "red", None

    now_bkk = now.astimezone(BANGKOK_TZ)
    if shift_override in ("เช้า", "ดึก"):
        current_shift = shift_override
    else:
        current_shift = "เช้า" if (8, 5) <= (now_bkk.hour, now_bkk.minute) < (20, 5) else "ดึก"
    shift_map = load_shift_map()

    for p in people:
        person_shift = shift_map.get(norm_name(p["username"]))

        if person_shift and person_shift != current_shift:
            # คนนี้ไม่ได้อยู่กะนี้ ไม่ต้องไปเช็คว่าเช็คชื่อรอบของกะนี้หรือไม่ (ไม่ใช่ภาระของเขา)
            p["checkin"] = {
                "round1": {"label": "ไม่ใช่กะนี้", "status": "offshift", "time": None},
                "round2": {"label": "ไม่ใช่กะนี้", "status": "offshift", "time": None},
                "round3": {"label": "ไม่ใช่กะนี้", "status": "offshift", "time": None},
            }
            continue

        r1_status, r1_time = round_status_for(p["user_id"], round1_label, is_round1=True)
        r2_status, r2_time = round_status_for(p["user_id"], round2_label)
        r3_status, r3_time = round_status_for(p["user_id"], round3_label)

        # ถ้าเช็คชื่อรอบหลังผ่านแล้ว ถือว่ารอบก่อนหน้าผ่านไปด้วยอัตโนมัติ (ไล่จากรอบท้ายสุดย้อนขึ้นไป)
        if r3_status == "green" and r2_status != "green":
            r2_status = "green"
            r2_time = r3_time
        if r2_status == "green" and r1_status != "green":
            r1_status = "green"
            r1_time = r2_time
        p["checkin"] = {
            "round1": {"label": "เช็คชื่อรอบที่ 1", "status": r1_status, "time": r1_time},
            "round2": {"label": "เช็คชื่อรอบที่ 2", "status": r2_status, "time": r2_time},
            "round3": {"label": "เช็คชื่อรอบที่ 3", "status": r3_status, "time": r3_time},
        }

    return {
        "people": people,
        "summary": {"out_now": out_count, "activity_counts_today": activity_counts},
        "current_shift": current_shift,
        "is_override": shift_override is not None,
    }


# ===== Shared snapshot สำหรับ SSE =====
# background thread เดียว query database แล้ว cache ไว้ ทุก SSE connection อ่านร่วมกัน
# ตื่นทันทีเมื่อมีข้อความใหม่จาก Telegram (เดิมต้องรอครบรอบ 1.5 วิ + SSE poll อีก 1.5 วิ)

_snapshot_cache = {}       # {shift_key: payload_str}
_snapshot_version = 0
_snapshot_cv = threading.Condition()
_wake_event = threading.Event()
_subs = defaultdict(int)   # {shift_key: จำนวนหน้าจอที่เปิดดูกะนั้นอยู่}
_subs_lock = threading.Lock()


def request_refresh():
    """สั่งให้ snapshot thread ทำงานรอบใหม่ทันที"""
    _wake_event.set()


def snapshot_updater():
    global _snapshot_version
    while True:
        try:
            # สร้างเฉพาะกะที่มีคนเปิดดูอยู่จริง (None = โหมดอัตโนมัติ สร้างเสมอ)
            with _subs_lock:
                keys = {None} | {k for k, n in _subs.items() if n > 0}

            results = {}
            with db() as cur:
                for key in keys:
                    results[key] = _json.dumps(
                        _build_status_payload(cur, key), ensure_ascii=False, default=str
                    )

            with _snapshot_cv:
                changed = any(_snapshot_cache.get(k) != v for k, v in results.items())
                _snapshot_cache.update(results)
                if changed:
                    _snapshot_version += 1
                    _snapshot_cv.notify_all()
        except Exception as e:
            print(f"snapshot error: {e}")

        # ปกติรอ SNAPSHOT_INTERVAL วินาที แต่ถ้ามีข้อความใหม่เข้ามาจะตื่นทันที
        _wake_event.wait(timeout=SNAPSHOT_INTERVAL)
        _wake_event.clear()


@app.route("/api/stream")
def api_stream():
    shift_override = request.args.get("shift")
    if shift_override not in ("เช้า", "ดึก"):
        shift_override = None

    def event_stream():
        with _subs_lock:
            _subs[shift_override] += 1
        request_refresh()  # สร้าง payload ของกะนี้ทันทีถ้ายังไม่มีใน cache

        last_version = -1
        last_payload = None
        try:
            while True:
                with _snapshot_cv:
                    have_new = _snapshot_cv.wait_for(
                        lambda: _snapshot_version != last_version, timeout=15
                    )
                    last_version = _snapshot_version
                    payload_str = _snapshot_cache.get(shift_override)

                if payload_str is not None and payload_str != last_payload:
                    yield f"data: {payload_str}\n\n"
                    last_payload = payload_str
                elif not have_new:
                    yield ": keepalive\n\n"  # กัน proxy ตัดการเชื่อมต่อตอนไม่มีความเคลื่อนไหว
        finally:
            with _subs_lock:
                _subs[shift_override] = max(0, _subs[shift_override] - 1)

    return Response(event_stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)


# ===== จุดเริ่มโปรแกรม =====

if __name__ == "__main__":
    init_db()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("API พร้อมใช้งานที่ http://localhost:5000/api/status")
    print(f"แสดงเฉพาะคนที่มีความเคลื่อนไหวภายใน {ACTIVE_DAYS} วันล่าสุด (ปรับที่ ACTIVE_DAYS)")
    if DASH_USER:
        print("เปิดใช้ล็อกอินเข้าแดชบอร์ดแล้ว")
    else:
        print("แดชบอร์ดเปิดให้เข้าดูได้เลย (ตั้ง DASH_USER/DASH_PASS ถ้าต้องการจำกัดสิทธิ์)")

    # background thread สร้าง snapshot สำหรับ SSE (query database ตัวเดียว)
    snapshot_thread = threading.Thread(target=snapshot_updater, daemon=True)
    snapshot_thread.start()

    print("กำลังฟังข้อความใหม่จากกลุ่ม Telegram... (กด Ctrl+C เพื่อหยุด)")
    with client:
        client.run_until_disconnected()
