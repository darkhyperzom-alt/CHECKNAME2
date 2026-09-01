# ระบบเช็คสถานะพนักงาน (Telegram → Dashboard)

## โครงสร้างไฟล์

| ไฟล์ | ใช้ทำอะไร |
|---|---|
| `main.py` | โปรแกรมหลัก (ฟัง Telegram + API + เสิร์ฟหน้าเว็บ) — ตัวนี้ตัวเดียวพอ |
| `dashboard.html` | หน้าแดชบอร์ด |
| `shift_calendar.html` | ตารางกะรายเดือน (เข้าที่ `/shift-calendar`) — ตัวกำหนดกะของแต่ละคนในแต่ละวันจริง |
| `requirements.txt` | รายชื่อไลบรารีที่ต้องติดตั้ง |
| `generate_session.py` | รันครั้งเดียวเพื่อสร้าง string session (ใช้ตอน deploy) |
| `cleanup.sql` | สคริปต์ล้างรายชื่อคนที่ออกไปแล้ว + ตรวจบั๊ก (รันทีละบล็อก) |
| `.env.example` | ตัวอย่างไฟล์ตั้งค่า (ไม่มีค่าจริง) |
| `.env` | ไฟล์ตั้งค่าจริงของคุณ — **ห้าม commit ขึ้น git** |
| `.gitignore` | กันไฟล์ลับ/ฐานข้อมูลไม่ให้หลุดขึ้น git |

> ⚠️ **เช็คชื่อไฟล์ `.gitignore` ให้ดี** — ต้องมีจุดนำหน้าและอยู่ที่ root ของ repo
> ถ้าเป็น `gitignore.txt` มันจะไม่ทำงานเลย ตรวจด้วย `git ls-files | grep -i gitignore`

## ตั้งค่า (Environment Variables)

**จำเป็นต้องมี**

| ชื่อ | ใช้ทำอะไร |
|---|---|
| `TG_API_ID` / `TG_API_HASH` | ข้อมูล API ของ Telegram |
| `TG_SESSION` | string session (จาก `generate_session.py`) — ใช้ตอน deploy |
| `TG_GROUP_ID` | กลุ่มที่ส่งข้อความกิจกรรม (กินข้าว/ปวดหนัก/ปวดน้อย/กลับที่นั่ง) |
| `TG_GROUP_ID_CHECKIN` | กลุ่มประกาศรอบ + เช็คชื่อ |
| `DATABASE_URL` | connection string ของ PostgreSQL |

**ไม่ใส่ก็ได้ (มีค่า default)**

| ชื่อ | default | ใช้ทำอะไร |
|---|---|---|
| `ACTIVE_DAYS` | `14` | คนที่ไม่มีความเคลื่อนไหวเกินกี่วัน ให้หายจากแดชบอร์ดเอง (ข้อมูลไม่ถูกลบ) |
| `DASH_USER` / `DASH_PASS` | ว่าง | ล็อกอินเข้าแดชบอร์ด **ถ้าเว้นว่าง = ใครมี URL ก็เข้าดู/แก้/ลบข้อมูลได้** |
| `CORS_ORIGINS` | ว่าง | เปิด CORS เฉพาะโดเมนที่ระบุ คั่นด้วยจุลภาค (เว้นว่าง = ปิด) |
| `DB_POOL_MAX` | `8` | จำนวน connection สูงสุดใน pool |

## ฐานข้อมูล

ใช้ **PostgreSQL** (ไม่ใช่ SQLite แล้ว) เพื่อให้ข้อมูลไม่หายตอน Render restart service

### รันที่เครื่องตัวเอง (ตัวเลือก)
ถ้าไม่มี PostgreSQL ที่เครื่องตัวเอง ใช้ Postgres ตัวเดียวกับที่ deploy บน Render ได้เลย (ใช้ "External Database URL" จากหน้า Render Postgres) — ใส่ใน `.env` ที่ `DATABASE_URL`

### Deploy บน Render
1. สร้าง Render Postgres (New → PostgreSQL) แยกจาก Web Service
2. คัดลอก **Internal Database URL**
3. ใส่เป็น Environment Variable ชื่อ `DATABASE_URL` ในหน้า Web Service

## รันที่เครื่องตัวเอง

```
pip install -r requirements.txt
python main.py
```
ครั้งแรกจะถาม OTP login Telegram ตามปกติ (อ่านค่า api_id/hash จากไฟล์ `.env`) และจะสร้างตารางใน PostgreSQL ให้อัตโนมัติถ้ายังไม่มี

เปิดเบราว์เซอร์ไปที่ `http://localhost:5000` จะเห็นแดชบอร์ด

## Deploy ขึ้น Render

1. สร้าง string session: `python generate_session.py`
2. Push ขึ้น GitHub (ไฟล์ `.env` จะไม่ติดไปด้วย เพราะ `.gitignore` กันไว้)
3. Render → New → Web Service → เชื่อม repo
   - Build command: `pip install -r requirements.txt`
   - Start command: `python main.py`
4. ใส่ Environment Variables ตามตารางด้านบน (อย่าลืม `DASH_USER` / `DASH_PASS`)
5. Deploy แล้วเข้าผ่าน URL ที่ Render ให้มา

## ข้อควรระวัง

- `TG_API_HASH`, `TG_SESSION`, `DATABASE_URL` คือข้อมูลที่ทำให้เข้าถึงบัญชี Telegram/ฐานข้อมูลได้ ห้ามแชร์/commit ขึ้น git โดยเด็ดขาด
- `.gitignore` กันได้เฉพาะ commit ใหม่ — ถ้าเคยเผลอ commit ไปแล้ว ค่ายังอยู่ใน history ตรวจด้วย

  ```bash
  git log --all --full-history --oneline -- .env "*.session" "*.db"
  git log -p --all -S "TG_SESSION" | head
  ```

  ถ้าเจอ ต้อง revoke ทันที: ยกเลิก session ใน Telegram (Settings → Devices), เปลี่ยนรหัสผ่าน Postgres, ออก `api_hash` ใหม่ — การลบไฟล์ทีหลังไม่ช่วยอะไร
- `shift_assignments.json` ไม่ควรอยู่ใน repo (มีรายชื่อพนักงานทั้งหมด) และไม่ได้ใช้แล้ว เพราะข้อมูลกะย้ายไปอยู่ใน PostgreSQL

  ```bash
  git rm --cached shift_assignments.json
  ```
- ด้วย PostgreSQL ข้อมูลจะไม่หายตอน restart แล้ว (ต่างจาก SQLite เดิม)

## สิ่งที่แก้ล่าสุด

**บั๊ก**
- ข้อความเตือนในกลุ่ม (เช่น "เกินเวลา… กรุณากลับที่นั่ง") เคยถูกอ่านเป็นการกดกลับจริง ทำให้คนที่ยังไม่กด ขึ้นเป็น "กลับที่นั่ง" เอง — กรองด้วย `NOISE_WORDS` แล้ว
- regex ชื่อผู้ใช้เคยไปจับบรรทัด "รหัสผู้ใช้" ถ้าบรรทัดนั้นมาก่อน — ใส่ `(?<!รหัส)` กันไว้
- กดเช็คชื่อมือ (ปุ่ม +) แล้วไม่ขึ้นสีเขียว เพราะป้ายรอบใช้ซ้ำทุกวัน แถวเดิมจากเมื่อวานทำให้ระบบไม่อัปเดตเวลาประกาศ
- กดเช็คชื่อมือตอนสลับดูกะอื่น เคยไปลงผิดกะ — ตอนนี้ส่งค่ากะที่เลือกอยู่ไปด้วย
- เช็คชื่อจาก Telegram เคยไปเกาะรอบของเมื่อวานแล้วเงียบหาย — จำกัดให้เฉพาะรอบในกะปัจจุบัน
- เทียบชื่อกับตารางกะเป็นแบบ case-sensitive และติดช่องว่างท้ายชื่อ ทำให้บางคนหากะไม่เจอแล้วขึ้นแดงทั้งที่ไม่ใช่กะเขา — normalize ชื่อก่อนเทียบแล้ว

**ความปลอดภัย**
- เพิ่ม Basic Auth (เปิดใช้ด้วย `DASH_USER` / `DASH_PASS`)
- ปิด CORS เป็นค่าเริ่มต้น (เดิมเปิดให้ทุกโดเมน)
- escape ชื่อผู้ใช้ก่อนแสดงผลทั้ง 2 หน้า กัน XSS

**ประสิทธิภาพ**
- ใช้ connection pool และปิด connection เสมอแม้ query พัง (เดิมเปิดใหม่ทุก query และรั่วเมื่อ error)
- snapshot thread ใช้ connection เดียวสร้างครบทั้ง 3 กะ (เดิมเปิด 6 ครั้งทุก 1.5 วินาที)
- cache ตารางกะ 60 วินาที
- เขียน DB ใน thread แยก ไม่บล็อก event loop ของ Telethon
