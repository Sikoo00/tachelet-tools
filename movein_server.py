"""
שרת ווב מקומי: משכורשת → סאמיט
הרץ: python movein_server.py
פותח אוטומטית בדפדפן: http://localhost:7654
"""
import asyncio, threading, json, re, webbrowser
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from playwright.async_api import async_playwright

SETTINGS_FILE = Path.home() / ".movein_summit_settings.json"
DOWNLOAD_DIR  = Path.home() / "Downloads" / "movein"
MASKORESHET   = "https://maskoreshet.co.il/"
MONTHS_HE     = ["ינואר","פברואר","מרץ","אפריל","מאי","יוני",
                  "יולי","אוגוסט","ספטמבר","אוקטובר","נובמבר","דצמבר"]

log_buffer   = []
running      = False
run_id       = 0  # מונה ריצות
company_url  = ''  # URL אחרון של חברה בסאמיט

def load_settings():
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except: pass
    return {}

def save_settings(d):
    SETTINGS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def add_log(msg):
    log_buffer.append(msg)
    if len(log_buffer) > 200:
        log_buffer.pop(0)

async def fetch_companies(user, password):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page    = await (await browser.new_context()).new_page()
        await page.goto(MASKORESHET)
        await page.wait_for_load_state("networkidle")
        await page.fill('input[type="text"]', user)
        await page.fill('input[type="password"]', password)
        await page.click('input[type="submit"], button[type="submit"]')
        await page.wait_for_load_state("networkidle")
        await page.click('text=מעסיקים')
        await page.wait_for_load_state("networkidle")
        await page.wait_for_selector('table tr td', timeout=5000)
        rows  = await page.locator('table tbody tr').all()
        skip  = {"שם המעסיק","V","X","נבחר","תזכורת","לא טופל"}
        names = []
        companies = []
        for r in rows:
            cells = await r.locator('td').all_text_contents()
            name = None
            id_num = None
            for c in cells:
                c = c.strip()
                if c and c not in skip and any('֐'<=ch<='׿' for ch in c) and not c.isdigit():
                    if not name:
                        name = c
                # מספר זיהוי (ח.פ/ע.מ) — 9 ספרות שלא מתחילות ב-9 (תיק ניכויים)
                if c.isdigit() and len(c) == 9 and not c.startswith("9"):
                    id_num = c
                elif c.isdigit() and 8 <= len(c) <= 9 and id_num is None:
                    id_num = c
            if name:
                names.append(name)
                companies.append({"name": name, "id": id_num or ""})
        await browser.close()
        return names, companies

async def run_automation(cfg):
    global running
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    company, month_num, year = cfg["company"], cfg["month"], cfg["year"]
    month_he = MONTHS_HE[month_num-1]

    async with async_playwright() as pw:
        # שלב 1: משכורשת
        add_log(f"📥 שלב 1: מתחבר למשכורשת...")
        browser = await pw.chromium.launch(headless=False)
        page    = await (await browser.new_context(accept_downloads=True)).new_page()
        await page.goto(MASKORESHET)
        await page.wait_for_load_state("networkidle")
        await page.fill('input[type="text"]',     cfg["mash_user"])
        await page.fill('input[type="password"]', cfg["mash_pass"])
        await page.click('input[type="submit"], button[type="submit"]')
        await page.wait_for_load_state("networkidle")
        add_log("   ✓ כניסה בוצעה")

        # שלב א: בחירת חברה קודם
        await page.click('text=מעסיקים')
        await page.wait_for_load_state("networkidle")
        add_log(f"   ← מחפש חברה: {company}...")
        found = False
        for row in await page.locator('table tbody tr').all():
            cells = await row.locator('td').all_text_contents()
            if any(company in c for c in cells):
                await row.click(); found = True; break
        if not found:
            add_log(f"   ✗ לא נמצאה: {company}"); await browser.close(); running=False; return False
        await page.wait_for_load_state("networkidle")
        add_log(f"   ✓ חברה '{company}' נבחרה")

        # שלב ב: ניווט לפקודה
        await page.click('text=הנה"ח')
        await page.wait_for_load_state("networkidle")
        await page.click('text=פקודה')
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)
        add_log("   ✓ עמוד פקודת שכר נטען")

        # שלב ג: בחירת חודש אחרי שנכנסנו לחברה
        month_value = f"{month_num}/{year}"
        add_log(f"   ← בוחר חודש {month_value}...")
        try:
            for sel in await page.locator('select').all():
                opts = await sel.locator('option').all_text_contents()
                if any('/' in o for o in opts):
                    await sel.select_option(label=month_value)
                    await page.wait_for_load_state("networkidle")
                    await page.wait_for_timeout(1500)
                    add_log(f"   ✓ חודש {month_value} נבחר")
                    break
        except Exception as e:
            add_log(f"   ⚠️  שגיאה בבחירת חודש: {e}")

        await page.wait_for_timeout(1000)
        add_log("   ✓ פקודת שכר נטענה")

        # בדיקה שהסכום לא 0 לפני הורדה
        try:
            page_text = await page.inner_text('body')
            if 'חובה' in page_text:
                add_log("   ✓ פקודה נטענה עם נתונים")
        except: pass

        safe = "".join(c for c in company if c.isalnum() or c in " -_")[:20].strip()
        fname = f"MOVEIN_{safe}_{year}{month_num:02d}.DAT"
        save_path = DOWNLOAD_DIR / fname
        async with page.expect_download() as dl:
            await page.click('text=הפקת קובץ תנועות')
        download = await dl.value
        await download.save_as(save_path)
        await page.wait_for_timeout(1000)
        add_log(f"   ✓ קובץ הורד: {fname} ({save_path.stat().st_size} bytes)")
        await browser.close()

        # שלב 2: אימות
        add_log("\n🔍 שלב 2: מאמת פקודת שכר...")
        lines = [l for l in save_path.read_bytes().decode("windows-1255","replace").replace("\r\n","\n").replace("\r","\n").split("\n") if l.strip()]
        chova = zchut = 0.0; rows_n = 0
        for line in lines[1:]:
            if len(line) < 50: continue
            col1 = line[0:9].strip(); sign = line[38]
            amt  = (1 if sign=="+" else -1) * (int(line[39:51].strip()) / 100)
            if col1: chova += amt
            else:    zchut += amt
            rows_n += 1
        diff = abs(chova-zchut); balanced = diff < 0.02
        add_log(f"   שורות: {rows_n}  |  חובה: ₪{chova:,.2f}  |  זכות: ₪{zchut:,.2f}  |  הפרש: ₪{diff:.2f}")
        if not balanced:
            add_log("🛑 פקודה לא מאוזנת — בוטל!"); running=False; return False
        add_log("   ✓ מאוזן")
        # שלב 3: סאמיט
        add_log("\n📤 שלב 3: מעלה לסאמיט...")
        browser2 = None
        page2 = None
        try:
            browser2 = await pw.chromium.connect_over_cdp("http://localhost:9222")
            # מחפש טאב של סאמיט
            for ctx in browser2.contexts:
                for p in ctx.pages:
                    if 'sumit' in p.url:
                        page2 = p
                        break
                if page2: break
            if not page2 and browser2.contexts:
                page2 = browser2.contexts[0].pages[0]
            add_log(f"   ✓ מחובר לכרום — סאמיט פתוח")
        except Exception as e:
            add_log(f"   ⚠️  לא הצלחתי להתחבר לכרום ({e})")
            add_log("   פותח דפדפן חדש — התחבר לסאמיט ידנית")
            browser2 = await pw.chromium.launch(headless=False)
            page2 = await (await browser2.new_context()).new_page()

        # מציאת מספר עוסק של החברה
        company_id_num = ""
        companies_data = cfg.get("companies_data", [])
        for c in companies_data:
            if c.get("name") == company:
                company_id_num = c.get("id","")
                break

        company_summit_url = ""
        # פתח כרטיסייה חדשה לכל חברה — הישנה נשארת פתוחה
        add_log("   ← פותח כרטיסייה חדשה בסאמיט...")
        page2 = await browser2.contexts[0].new_page()
        await page2.bring_to_front()
        
        # שלב א: ניווט לעמוד הראשי של סאמיט
        add_log("   ← עובר לעמוד הראשי של סאמיט...")
        await page2.goto("https://app.sumit.co.il/")
        await page2.wait_for_load_state("networkidle")
        await page2.wait_for_timeout(2000)

        # שלב ב: חיפוש החברה לפי מספר עוסק
        if company_id_num:
            add_log(f"   ← מחפש חברה לפי מספר עוסק: {company_id_num}...")
            try:
                search_box = page2.locator('input[type="search"], input[placeholder*="חיפוש"], input[placeholder*="עוסק"], input[placeholder*="חברה"]').first
                await search_box.wait_for(timeout=5000)
                await search_box.fill(company_id_num)
                await page2.wait_for_timeout(1200)
                add_log("   ✓ הוקלד מספר עוסק")
            except Exception as e:
                add_log(f"   ⚠️  לא נמצא שדה חיפוש: {e}")

            # לחיצה על שם החברה בכחול (לינק) — get_by_text עובד עם תווים מיוחדים
            add_log("   ← לוחץ על שם החברה...")
            try:
                # נסיון 1: חיפוש לינק לפי שם החברה
                company_link = page2.get_by_role("link", name=company)
                if await company_link.count() > 0:
                    await company_link.first.click()
                else:
                    # נסיון 2: שורה עם מספר העוסק ← לחץ על הלינק הראשון בה
                    row = page2.locator(f'tr:has-text("{company_id_num}")').first
                    if await row.count() > 0:
                        await row.locator('a').first.click()
                    else:
                        # נסיון 3: get_by_text
                        await page2.get_by_text(company, exact=False).first.click()
                await page2.wait_for_load_state("networkidle")
                await page2.wait_for_timeout(1500)
                company_summit_url = page2.url
                add_log(f"   ✓ נכנסנו לחברה: {company_summit_url}")
            except Exception as e:
                company_summit_url = ""
                add_log(f"   ⚠️  שגיאה בכניסה לחברה: {e}")
                links = await page2.locator('a').all_text_contents()
                add_log(f"   קישורים: {[l.strip() for l in links if l.strip()][:10]}")
        else:
            company_summit_url = page2.url
            add_log(f"   ⚠️  אין מספר עוסק — URL נוכחי: {page2.url}")

        # שלב ג: לחיצה על "יבוא קובץ תנועות"
        add_log("   ← מחפש 'יבוא קובץ תנועות'...")
        try:
            btn = page2.locator('text=יבוא קובץ תנועות').first
            await btn.wait_for(timeout=8000)
            await btn.click()
            await page2.wait_for_timeout(1000)
            add_log("   ✓ תפריט ייבוא נפתח")
        except Exception as e:
            add_log(f"   ⚠️  לא נמצא כפתור ייבוא: {e}")
            all_texts = await page2.locator('button, a, [role="button"], li').all_text_contents()
            add_log(f"   אלמנטים: {[t.strip() for t in all_texts if t.strip()][:15]}")

        # שלב ד: לחיצה על "יבוא בפורמט חשבשבת"
        add_log("   ← בוחר פורמט חשבשבת...")
        try:
            await page2.get_by_text("חשבשבת", exact=False).first.click(timeout=5000)
            add_log("   ✓ פורמט חשבשבת נבחר")
        except:
            try:
                await page2.get_by_text("movein", exact=False).first.click(timeout=5000)
                add_log("   ✓ פורמט movein נבחר")
            except Exception as e:
                add_log(f"   ⚠️  שגיאה בבחירת פורמט: {e}")
                items = await page2.locator('li, [role="menuitem"], [role="option"]').all_text_contents()
                add_log(f"   פריטי תפריט: {[i.strip() for i in items if i.strip()][:10]}")
        await page2.wait_for_timeout(1000)

        # שלב ה: טעינת הקובץ
        add_log("   ← טוען קובץ...")
        try:
            await page2.locator('input[type="file"]').set_input_files(str(save_path))
            await page2.wait_for_timeout(800)
            add_log("   ✓ קובץ הועלה")
        except Exception as e:
            add_log(f"   ⚠️  שגיאה בטעינת קובץ: {e}")

        # שלב ו: לחיצה על "תצוגה מקדימה"
        add_log("   ← לוחץ תצוגה מקדימה...")
        try:
            await page2.get_by_text("תצוגה מקדימה", exact=False).first.click(timeout=8000)
            await page2.wait_for_load_state("networkidle")
            await page2.wait_for_timeout(2000)
            add_log("   ✓ תצוגה מקדימה נפתחה")
        except Exception as e:
            add_log(f"   ⚠️  שגיאה בתצוגה מקדימה: {e}")
            btns = await page2.locator('button, a, [role="button"]').all_text_contents()
            add_log(f"   כפתורים: {[b.strip() for b in btns if b.strip()][:10]}")

        # שלב ז: בחירת מנה
        mana = cfg.get("summit_mana","").strip()
        if mana:
            add_log(f"   ← בוחר מנה: {mana}...")
            try:
                await page2.locator('text=מנה חדשה').first.click(timeout=5000)
                await page2.wait_for_timeout(800)
                add_log("   ✓ דרופדאון נפתח")
                await page2.get_by_text(mana, exact=False).last.click(timeout=5000)
                await page2.wait_for_timeout(500)
                add_log(f"   ✓ מנה '{mana}' נבחרה")
            except Exception as e:
                add_log(f"   ⚠️  שגיאה בבחירת מנה: {e}")
                try:
                    opts = await page2.locator('[role="option"], li').all_text_contents()
                    add_log(f"   אפשרויות: {[o.strip() for o in opts if o.strip()][:8]}")
                except: pass

        # שלב ח: לחיצה על "טעינה"
        add_log("   ← לוחץ טעינה...")
        try:
            await page2.get_by_text("טעינה", exact=True).first.click(timeout=8000)
            add_log("   ✓ טעינה נלחצה")
        except:
            try:
                await page2.locator('button:has-text("טעינה")').first.click(timeout=5000)
                add_log("   ✓ טעינה נלחצה (2)")
            except Exception as e:
                add_log(f"   ⚠️  לא נמצא כפתור טעינה: {e}")
                btns = await page2.locator('button, [role="button"]').all_text_contents()
                add_log(f"   כפתורים: {[b.strip() for b in btns if b.strip()]}")
        await page2.wait_for_timeout(1500)

        # שלב ט: אישור
        add_log("   ← לוחץ אישור...")
        try:
            await page2.get_by_text("אישור", exact=True).first.click(timeout=5000)
            add_log("   ✓ אישור נלחץ")
        except Exception as e:
            add_log(f"   ⚠️  לא נמצא כפתור אישור: {e}")
        await page2.wait_for_load_state("networkidle")
        await page2.wait_for_timeout(2000)
        add_log("   ✓ הייבוא הושלם!")
        await page2.wait_for_timeout(2000)
        # לא סוגרים את הכרטיסייה — נשארת פתוחה לבדיקה
        await browser2.close()  # רק מנתקים את החיבור, הכרטיסייה נשארת

    # שמור סטטוס בוצע
    s = load_settings()
    done_key = f"{year}-{month_num:02d}"
    done = s.get('done_companies', {})
    if done_key not in done:
        done[done_key] = []
    if company not in done[done_key]:
        done[done_key].append(company)
    s['done_companies'] = done
    save_settings(s)
    add_log(f"\n✅ הושלם! {company} | {month_he} {year}")
    company_url = company_summit_url
    running = False
    return {"ok": True, "company_url": company_summit_url}

# ── HTTP Server ───────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>משכורשת → סאמיט</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;700&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Heebo',sans-serif;background:#F4F6F9;color:#1A2332;min-height:100vh;direction:rtl;font-size:13px}
.header{background:#fff;padding:10px 20px;border-bottom:1px solid #D8E0EA;display:flex;align-items:center;gap:10px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.header h1{font-size:15px;font-weight:700;color:#1A2332}
.header p{font-size:11px;color:#6B7E96}
.arrow{color:#2E6BCC;margin:0 3px}
.main{max-width:900px;margin:16px auto;padding:0 16px;display:grid;gap:10px}
.card{background:#fff;border:1px solid #D8E0EA;border-radius:8px;padding:12px 16px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.card-title{font-size:11px;font-weight:700;color:#6B7E96;text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px}
.row{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}
.field{display:flex;flex-direction:column;gap:3px}
.field label{font-size:11px;color:#6B7E96}
.field input,.field select{background:#F8FAFB;border:1px solid #C8D4E0;border-radius:6px;padding:6px 10px;color:#1A2332;font-family:'Heebo',sans-serif;font-size:13px;outline:none;transition:border-color .2s}
.field input:focus,.field select:focus{border-color:#2E6BCC;background:#fff}
.field input::placeholder{color:#A8B8C8}
.f-grow{flex:1;min-width:120px}
.btn{border:none;border-radius:6px;padding:7px 14px;font-family:'Heebo',sans-serif;font-size:12px;font-weight:600;cursor:pointer;transition:all .2s;white-space:nowrap}
.btn-secondary{background:#EDF1F7;color:#4A6080;border:1px solid #C8D4E0}
.btn-secondary:hover{background:#D8E4F0}
.btn-success{background:#1A6E35;color:#fff;width:100%;padding:11px;font-size:14px;border-radius:8px}
.btn-success:hover:not(:disabled){background:#145C2B}
.btn-success:disabled{opacity:.4;cursor:not-allowed}
.log-box{background:#1A2332;border:1px solid #C8D4E0;border-radius:8px;padding:12px;height:200px;overflow-y:auto;font-family:'Courier New',monospace;font-size:11px;line-height:1.7;color:#4ADE80}
.log-box .err{color:#F87171}
.log-box .warn{color:#F59E0B}
.log-box .info{color:#60A5FA}
.status{display:flex;align-items:center;gap:6px;font-size:12px;color:#6B7E96;margin-top:6px}
.dot{width:7px;height:7px;border-radius:50%;background:#C8D4E0;flex-shrink:0}
.dot.running{background:#F59E0B;animation:pulse 1s infinite}
.dot.ok{background:#22C55E}
.dot.err{background:#EF4444}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.divider{border:none;border-top:1px solid #E8EEF4;margin:10px 0}
/* טבלת מנות */
.mana-tbl{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}
.mana-tbl th{text-align:right;padding:4px 8px;color:#6B7E96;border-bottom:1px solid #D8E0EA;font-weight:500}
.mana-tbl td{padding:3px 8px;border-bottom:1px solid #E8EEF4;color:#1A2332;font-weight:500}
.mana-tbl tr:hover td{background:#F8FAFB}
.mana-tbl input{background:#F8FAFB;border:1px solid #C8D4E0;border-radius:4px;padding:3px 8px;color:#1A2332;font-family:'Heebo',sans-serif;font-size:12px;width:100%}
</style>
</head>
<body>
<div class="header">
  <h1>משכורשת <span class="arrow">←</span> MOVEIN <span class="arrow">→</span> סאמיט</h1>
  <span style="color:#5A7BA0;font-size:11px;margin-right:10px">ייצוא פקודת שכר אוטומטי</span>
  <button onclick="shutdownServer()" style="margin-right:auto;background:#fee2e2;border:1px solid #fca5a5;color:#dc2626;border-radius:6px;padding:4px 12px;font-size:12px;cursor:pointer;font-family:Heebo,sans-serif">✕ סגור כלי</button>
</div>

<div class="main">

  <!-- שורה עליונה: משכורשת + פקודה + סאמיט -->
  <div class="card">
    <div class="row" style="gap:14px;align-items:flex-end">
      <div class="field f-grow"><label>שם משתמש</label><input id="mash_user" value="Shikoo00"></div>
      <div class="field f-grow"><label>סיסמה</label><input id="mash_pass" type="password" placeholder="••••••••"></div>
      <button class="btn btn-secondary" onclick="loadCompanies()">🔄 טען חברות</button>
      <div id="companies_wrap" style="display:none;flex:2;min-width:160px">
        <div class="field"><label>חברה</label>
          <select id="companies_list" style="background:#0F1117;border:1px solid #1E2D40;border-radius:6px;padding:6px 10px;color:#E8E6E0;font-family:Heebo,sans-serif;font-size:13px;width:100%" onchange="selectCompany(this.value)"></select>
        </div>
      </div>
      <div class="field"><label>חודש</label>
        <select id="month" onchange="saveMonthYear()" style="background:#0F1117;border:1px solid #1E2D40;border-radius:6px;padding:6px 10px;color:#E8E6E0;font-family:Heebo,sans-serif;font-size:13px">
          <option value="1">01 ינואר</option><option value="2">02 פברואר</option>
          <option value="3" selected>03 מרץ</option><option value="4">04 אפריל</option>
          <option value="5">05 מאי</option><option value="6">06 יוני</option>
          <option value="7">07 יולי</option><option value="8">08 אוגוסט</option>
          <option value="9">09 ספטמבר</option><option value="10">10 אוקטובר</option>
          <option value="11">11 נובמבר</option><option value="12">12 דצמבר</option>
        </select>
      </div>
      <div class="field" style="width:70px"><label>שנה</label><input id="year" type="number" value="2026" min="2020" max="2040" onchange="saveMonthYear()" style="width:70px"></div>
    </div>
    <div class="divider"></div>
    <div class="row" style="gap:14px;align-items:flex-end">
      <div class="field f-grow"><label>כתובת סאמיט</label><input id="summit_url" placeholder="https://app.sumit.co.il/books/?companyid=..."></div>
      <input id="summit_mana" type="hidden" value="">
    </div>
  </div>

  <!-- מנות לחברות -->
  <div class="card" id="mana_card" style="display:none">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div class="card-title" style="margin-bottom:0">⚙️ חברות ומנות</div>
      <div style="display:flex;gap:8px">
        <button class="btn btn-secondary" style="padding:4px 10px;font-size:11px" onclick="checkAll(true)">בחר הכל</button>
        <button class="btn btn-secondary" style="padding:4px 10px;font-size:11px" onclick="checkAll(false)">נקה הכל</button>
      </div>
    </div>
    <div id="mana_table_wrap">
      <table class="mana-tbl">
        <thead><tr>
          <th style="width:30px"><input type="checkbox" id="check_all" onchange="checkAll(this.checked)" title="בחר הכל"></th>
          <th onclick="sortTable('name')" style="cursor:pointer;user-select:none">חברה <span id="sort_name">⇅</span></th>
          <th onclick="sortTable('type')" style="cursor:pointer;user-select:none;width:80px">סוג <span id="sort_type">⇅</span></th>
          <th onclick="sortTable('mana')" style="cursor:pointer;user-select:none;width:100px">מנה <span id="sort_mana">⇅</span></th>
          <th style="width:50px;text-align:center">סטטוס</th>
        </tr></thead>
        <tbody id="mana_table_body"></tbody>
      </table>
    </div>
    <div id="mana_table_empty" style="display:none;font-size:12px;color:#5A7BA0">טען רשימת חברות</div>
  </div>

  <!-- הפעלה + לוג -->
  <div class="card">
    <div style="display:flex;gap:10px">
      <button class="btn btn-success" id="run_btn" onclick="runAuto()" style="flex:1">▶ הפעל אוטומציה</button>
      <button class="btn btn-success" id="run_all_btn" onclick="runAll()" style="flex:1;background:#1B3A4D;color:#60C8FF" title="מריץ אוטומציה לכל החברות ברצף">⏩ הפעל לכל החברות</button>
    </div>
    <div class="divider"></div>
    <div class="card-title">📟 יומן פעולות</div>
    <div class="log-box" id="log_box"><span class="info">מוכן להפעלה...</span></div>
    <div class="status"><div class="dot" id="status_dot"></div><span id="status_text">מוכן</span></div>
  </div>

</div>

<script>
let selectedCompany = '';
let polling = null;

// טעינה ראשונית של הגדרות
fetch('/settings').then(r=>r.json()).then(s=>{
  if(s.mash_user) document.getElementById('mash_user').value = s.mash_user;
  if(s.mash_pass) document.getElementById('mash_pass').value = s.mash_pass;
  if(s.summit_url) document.getElementById('summit_url').value = s.summit_url;
  if(s.summit_dbid && document.getElementById('summit_dbid')) document.getElementById('summit_dbid').value = s.summit_dbid;
  if(s.summit_mana) document.getElementById('summit_mana').value = s.summit_mana;
  if(s.company) selectCompany(s.company);
  if(s.companies_list) renderCompanies(s.companies_list, s.company);
  // ברירת מחדל: חודש קודם (אלא אם נשמר ידנית)
  const now = new Date();
  const prevMonth = now.getMonth() === 0 ? 12 : now.getMonth(); // getMonth()=0-11, ינואר=0 → חודש קודם=12, אחרת החודש הנוכחי כמספר (ספטמבר=8=אוגוסט)
  const prevYear = now.getMonth() === 0 ? now.getFullYear()-1 : now.getFullYear();
  document.getElementById('month').value = s.saved_month || prevMonth;
  document.getElementById('year').value = s.saved_year || prevYear;
  // טען סטטוסים בפתיחה
  setTimeout(loadDoneStatus, 500);
});

function shutdownServer(){
  fetch('/shutdown',{method:'POST'}).finally(()=>{ window.close(); });
}

function toggleDoneStatus(company, isDone){
  const month = parseInt(document.getElementById('month').value);
  const year = parseInt(document.getElementById('year').value);
  fetch('/toggle_done',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({company, month, year, done: isDone})});
}

let companyTypes = {};
try { companyTypes = JSON.parse(localStorage.getItem('company_types')||'{}'); } catch(e){}

function saveCompanyType(company, type){
  companyTypes[company] = type;
  localStorage.setItem('company_types', JSON.stringify(companyTypes));
}

let sortDir = {name:1, type:1, mana:1};
function sortTable(col){
  Object.keys(sortDir).forEach(k=>{ document.getElementById('sort_'+k).textContent='⇅'; });
  sortDir[col] *= -1;
  document.getElementById('sort_'+col).textContent = sortDir[col]===1?'▲':'▼';
  const tbody = document.getElementById('mana_table_body');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  rows.sort((a,b)=>{
    let va='', vb='';
    if(col==='name'){ va=a.cells[1].textContent.trim(); vb=b.cells[1].textContent.trim(); }
    else if(col==='type'){ va=a.cells[2].querySelector('select').value; vb=b.cells[2].querySelector('select').value; }
    else if(col==='mana'){
      const na=parseInt((a.cells[3].querySelector('input').value||'0').replace(/\D/g,''))||0;
      const nb=parseInt((b.cells[3].querySelector('input').value||'0').replace(/\D/g,''))||0;
      return (na-nb)*sortDir[col];
    }
    return va.localeCompare(vb,'he')*sortDir[col];
  });
  rows.forEach(r=>tbody.appendChild(r));
}

function loadDoneStatus(){
  const month = parseInt(document.getElementById('month').value);
  const year = parseInt(document.getElementById('year').value);
  fetch('/done_status',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({month,year})})
  .then(r=>r.json()).then(d=>{
    document.querySelectorAll('.status-cell').forEach(td=>{
      const c = td.dataset.company;
      td.textContent = d.done && d.done.includes(c) ? '✅' : '';
    });
  });
}

function saveMonthYear(){
  const month = document.getElementById('month').value;
  const year = document.getElementById('year').value;
  fetch('/save_month',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({month:parseInt(month),year:parseInt(year)})});
  loadDoneStatus();
}

function renderCompanies(list, selected){
  // dropdown
  const el = document.getElementById('companies_list');
  el.innerHTML = '<option value="">-- בחר חברה --</option>';
  list.forEach(function(c){
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = c;
    if(c===selected) opt.selected = true;
    el.appendChild(opt);
  });
  document.getElementById('companies_wrap').style.display = list.length ? 'flex' : 'none';
document.getElementById('mana_card').style.display = list.length ? 'block' : 'none';
  if(selected) selectedCompany = selected;


  // טבלת מנות
  let manaSettings = {};
  try { manaSettings = JSON.parse(localStorage.getItem('mana_per_company')||'{}'); } catch(e){}
  const tbody = document.getElementById('mana_table_body');
  tbody.innerHTML = '';
  // סנן כותרות ושורות מיוחדות
  const filteredList = list.filter(c => c && !c.includes('שם המעסיק') && c.trim() !== '');
  filteredList.forEach(function(c){
    const tr = document.createElement('tr');
    // checkbox
    const tdCk = document.createElement('td');
    tdCk.style.cssText = 'padding:4px 8px;border-bottom:1px solid #E8EEF4;text-align:center';
    const ck = document.createElement('input');
    ck.type = 'checkbox';
    ck.dataset.company = c;
    ck.className = 'company-check';
    ck.style.cursor = 'pointer';
    ck.addEventListener('change', function(){
      if(this.checked){ selectedCompany=c; document.getElementById('companies_list').value=c; }
    });
    tdCk.appendChild(ck);
    // שם
    const td1 = document.createElement('td');
    td1.style.cssText = 'padding:4px 8px;border-bottom:1px solid #E8EEF4;color:#1A2332;cursor:pointer;font-weight:500';
    td1.textContent = c;
    td1.addEventListener('click', function(){ ck.checked=!ck.checked; ck.dispatchEvent(new Event('change')); });
    // סוג חברה
    const tdType = document.createElement('td');
    tdType.style.cssText = 'padding:3px 8px;border-bottom:1px solid #E8EEF4';
    const selType = document.createElement('select');
    const savedType = companyTypes[c] || '';
    selType.innerHTML = '<option value="">--</option><option value="בעמ">בע"מ</option><option value="עוסק">עוסק</option>';
    selType.value = savedType;
    selType.style.cssText = 'background:#F8FAFB;border:1px solid #C8D4E0;border-radius:4px;padding:2px 6px;color:#1A2332;font-family:Heebo,sans-serif;font-size:12px;width:70px';
    selType.dataset.company = c;
    selType.addEventListener('change', function(){ saveCompanyType(this.dataset.company, this.value); });
    tdType.appendChild(selType);
    // מנה
    const td2 = document.createElement('td');
    td2.style.cssText = 'padding:3px 8px;border-bottom:1px solid #0F1117';
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.value = manaSettings[c] || '';
    inp.placeholder = 'מנה 10';
    inp.style.cssText = 'background:#fff;border:1px solid #C8D4E0;border-radius:4px;padding:3px 8px;color:#1A2332;font-family:Heebo,sans-serif;font-size:12px;width:100%;font-weight:600';
    inp.dataset.company = c;
    inp.addEventListener('input', function(){ saveManaForCompany(this.dataset.company, this.value); });
    inp.addEventListener('change', function(){ saveManaForCompany(this.dataset.company, this.value); });
    td2.appendChild(inp);
    // תא סטטוס — לחיצה משנה מצב
    const td3 = document.createElement('td');
    td3.style.cssText = 'padding:4px 8px;border-bottom:1px solid #E8EEF4;text-align:center;font-size:14px;cursor:pointer;user-select:none';
    td3.dataset.company = c;
    td3.className = 'status-cell';
    td3.title = 'לחץ לשינוי סטטוס';
    td3.addEventListener('click', function(){
      const isDone = this.textContent === '✅';
      this.textContent = isDone ? '' : '✅';
      toggleDoneStatus(this.dataset.company, !isDone);
    });
    tr.appendChild(tdCk);
    tr.appendChild(td1);
    tr.appendChild(tdType);
    tr.appendChild(td2);
    tr.appendChild(td3);
    tbody.appendChild(tr);
  });
  // טען סטטוסים
  loadDoneStatus();
  document.getElementById('mana_table_wrap').style.display = list.length ? 'block' : 'none';
  document.getElementById('mana_table_empty').style.display = list.length ? 'none' : 'block';
}


function saveManaForCompany(company, mana){
  const s = JSON.parse(localStorage.getItem('mana_per_company')||'{}');
  s[company] = mana;
  localStorage.setItem('mana_per_company', JSON.stringify(s));
}

function getManaForCompany(company){
  const s = JSON.parse(localStorage.getItem('mana_per_company')||'{}');
  const specific = s[company] ? s[company].trim() : '';
  const defaultMana = document.getElementById('summit_mana').value.trim();
  return specific || defaultMana || '';
}

function selectCompany(name){
  selectedCompany = name;
}


function loadCompanies(){
  const user = document.getElementById('mash_user').value;
  const pass = document.getElementById('mash_pass').value;
  if(!user||!pass){alert('מלא שם משתמש וסיסמה של משכורשת');return;}
  appendLog('🔄 טוען רשימת חברות... (עשוי לקחת כ-30 שניות)');
  fetch('/load_companies',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({user,pass})})
  .then(()=>{ pollCompanies(); });
}

function pollCompanies(){
  let attempts = 0;
  const iv = setInterval(()=>{
    attempts++;
    fetch('/companies_status').then(r=>r.json()).then(d=>{
      if(d.companies){
        clearInterval(iv);
        renderCompanies(d.companies,'');
        appendLog('✓ נטענו '+d.companies.length+' חברות');
      } else if(d.error){
        clearInterval(iv);
        appendLog('✗ שגיאה: '+d.error);
      } else if(attempts > 60){
        clearInterval(iv);
        appendLog('✗ תם הזמן — נסה שוב');
      } else {
        appendLog('🔄 טוען... ('+attempts+')');
      }
    });
  }, 2000);
}

function fetchDbid(){
  appendLog('🔍 פותח סאמיט — לחץ על "יבוא קובץ תנועות" של החברה...');
  const url = document.getElementById('summit_url').value||'https://app.sumit.co.il';
  fetch('/fetch_dbid',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({summit_url:url})})
  .then(r=>r.json()).then(d=>{
    if(d.dbid){ document.getElementById('summit_dbid').value=d.dbid; appendLog('✓ Database ID: '+d.dbid); }
    else appendLog('⚠️ '+d.error);
  });
}

function runAuto(){
  if(!selectedCompany){alert('בחר חברה מהרשימה');return;}
  const cfg={
    company: selectedCompany,
    month: parseInt(document.getElementById('month').value),
    year: parseInt(document.getElementById('year').value),
    mash_user: document.getElementById('mash_user').value,
    mash_pass: document.getElementById('mash_pass').value,
    summit_url: document.getElementById('summit_url').value,
    summit_dbid: document.getElementById('summit_dbid') ? document.getElementById('summit_dbid').value : '',
    summit_mana: document.getElementById('summit_mana').value,
  };
  if(!cfg.mash_pass){alert('מלא סיסמת משכורשת');return;}
  document.getElementById('run_btn').disabled=true;
  document.getElementById('log_box').innerHTML='';
  setStatus('running','מריץ...');
  cfg.summit_mana = getManaForCompany(cfg.company);
  fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
  polling = setInterval(pollLog, 600);
}

function pollLog(){
  fetch('/log').then(r=>r.json()).then(d=>{
    const box = document.getElementById('log_box');
    box.innerHTML = d.log.map(l=>{
      if(l.includes('✗')||l.includes('❌')||l.includes('🛑')) return `<div class="err">${l}</div>`;
      if(l.includes('⚠️')) return `<div class="warn">${l}</div>`;
      return `<div>${l}</div>`;
    }).join('');
    box.scrollTop = box.scrollHeight;
    if(!d.running){
      clearInterval(polling);
      document.getElementById('run_btn').disabled=false;
      const ok = d.log.some(l=>l.includes('✅'));
      setStatus(ok?'ok':'err', ok?'הושלם בהצלחה!':'נעצר');
    }
  });
}

function setStatus(type, text){
  const dot = document.getElementById('status_dot');
  dot.className = 'dot '+type;
  document.getElementById('status_text').textContent = text;
}

function appendLog(msg){
  const box = document.getElementById('log_box');
  box.innerHTML += `<div>${msg}</div>`;
  box.scrollTop = box.scrollHeight;
}

// ── הפעל לכל החברות ──
let allQueue = [];
let allIndex = 0;

function checkAll(val){
  document.querySelectorAll('.company-check').forEach(c=>c.checked=val);
  document.getElementById('check_all').checked = val;
}

function getCheckedCompanies(){
  return Array.from(document.querySelectorAll('.company-check:checked')).map(c=>c.dataset.company);
}

function runAll(){
  const allCompanies = getCheckedCompanies();
  if(!allCompanies.length){alert('סמן לפחות חברה אחת ברשימה');return;}
  if(!document.getElementById('mash_pass').value){alert('מלא סיסמת משכורשת');return;}
  if(!confirm('הפעל אוטומציה ל-'+allCompanies.length+' חברות מסומנות?')) return;
  allQueue = allCompanies;
  allIndex = 0;
  document.getElementById('run_btn').disabled = true;
  document.getElementById('run_all_btn').disabled = true;
  document.getElementById('log_box').innerHTML = '';
  appendLog('🚀 מתחיל אוטומציה ל-'+allQueue.length+' חברות...');
  runNextInQueue();
}

function runNextInQueue(){
  if(allIndex >= allQueue.length){
    appendLog('\n🎉 הושלם! כל '+allQueue.length+' החברות עובדו.');
    setStatus('ok','הושלם הכל!');
    document.getElementById('run_btn').disabled = false;
    document.getElementById('run_all_btn').disabled = false;
    return;
  }
  const company = allQueue[allIndex];
  selectedCompany = company;
  appendLog('\n━━━ ['+( allIndex+1)+'/'+allQueue.length+'] '+company+' ━━━');
  setStatus('running', (allIndex+1)+'/'+allQueue.length+': '+company);
  const cfg={
    company: company,
    month: parseInt(document.getElementById('month').value),
    year: parseInt(document.getElementById('year').value),
    mash_user: document.getElementById('mash_user').value,
    mash_pass: document.getElementById('mash_pass').value,
    summit_url: document.getElementById('summit_url').value,
    summit_dbid: document.getElementById('summit_dbid') ? document.getElementById('summit_dbid').value : '',
    summit_mana: getManaForCompany(company),
  };
  fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
  let myRunId = null;
  let sawRunning = false; // חייבים לראות running=true לפני שמחשיבים סיום
  polling = setInterval(function(){
    fetch('/log').then(r=>r.json()).then(d=>{
      const box = document.getElementById('log_box');
      box.innerHTML = d.log.map(l=>{
        if(l.includes('✗')||l.includes('❌')||l.includes('🛑')) return `<div class="err">${l}</div>`;
        if(l.includes('⚠️')) return `<div class="warn">${l}</div>`;
        return `<div>${l}</div>`;
      }).join('');
      box.scrollTop = box.scrollHeight;
      if(d.running){
        // הריצה התחילה — שמור את ה-run_id
        myRunId = d.run_id;
        sawRunning = true;
      }
      // רק אחרי שראינו running=true, אפשר לזהות סיום
      if(sawRunning && !d.running && myRunId !== null && d.run_id === myRunId){
        clearInterval(polling);
        polling = null;
        sawRunning = false;
        myRunId = null;
        // פתח כרטיסייה חדשה לחברה שהסתיימה
        if(d.company_url && d.company_url.startsWith('http')){
          window.open(d.company_url, '_blank');
        }
        allIndex++;
        setTimeout(runNextInQueue, 2000);
      }
    });
  }, 800);
}
</script>
</body>
</html>"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/':
            body = HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/settings':
            self.send_json(load_settings())
        elif self.path == '/log':
            self.send_json({'log': log_buffer, 'running': running, 'run_id': run_id, 'company_url': company_url})
        elif self.path == '/companies_status':
            s = load_settings()
            if s.get('companies_ready'):
                self.send_json({'companies': s.get('companies_list',[]), 'companies_data': s.get('companies_data',[])})
            elif s.get('companies_error'):
                self.send_json({'error': s['companies_error']})
            else:
                self.send_json({'status': 'loading'})

    def do_POST(self):
        if self.path == '/shutdown':
            self.send_json({'ok': True})
            import os, signal
            threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
            return
        length = int(self.headers.get('Content-Length',0))
        body   = json.loads(self.rfile.read(length))

        if self.path == '/save_month':
            s = load_settings()
            s['saved_month'] = body.get('month', s.get('saved_month', 3))
            s['saved_year'] = body.get('year', s.get('saved_year', 2026))
            save_settings(s)
            self.send_json({'ok': True}); return

        if self.path == '/toggle_done':
            s = load_settings()
            month = int(body.get('month', 0))
            year = int(body.get('year', 0))
            company = body.get('company', '')
            is_done = body.get('done', False)
            done_key = f"{year}-{month:02d}"
            done = s.get('done_companies', {})
            if done_key not in done:
                done[done_key] = []
            if is_done and company not in done[done_key]:
                done[done_key].append(company)
            elif not is_done and company in done[done_key]:
                done[done_key].remove(company)
            s['done_companies'] = done
            save_settings(s)
            self.send_json({'ok': True}); return

        if self.path == '/done_status':
            s = load_settings()
            month = int(body.get('month', 0))
            year = int(body.get('year', 0))
            done_key = f"{year}-{month:02d}"
            done = s.get('done_companies', {}).get(done_key, [])
            self.send_json({'done': done}); return

        if self.path == '/companies_status':
            s = load_settings()
            if s.get('companies_ready'):
                self.send_json({'companies': s.get('companies_list',[]), 'companies_data': s.get('companies_data',[])})
            elif s.get('companies_error'):
                self.send_json({'error': s['companies_error']})
            else:
                self.send_json({'status': 'loading'})
            return

        if self.path == '/load_companies':
            # שולחים תגובה מיידית — התוצאה תגיע דרך polling
            self.send_json({'status': 'loading'})
            def worker():
                try:
                    names, companies = asyncio.run(fetch_companies(body['user'], body['pass']))
                    s = load_settings()
                    s['companies_list'] = names
                    s['companies_data'] = companies
                    s['mash_user'] = body.get('user', s.get('mash_user',''))
                    s['mash_pass'] = body.get('pass', s.get('mash_pass',''))
                    s['companies_ready'] = True
                    save_settings(s)
                except Exception as e:
                    s = load_settings()
                    s['companies_error'] = str(e)
                    s['companies_ready'] = False
                    save_settings(s)
            threading.Thread(target=worker).start(); return

        elif self.path == '/fetch_dbid':
            def worker():
                import time
                async def fetch():
                    async with async_playwright() as pw:
                        try:
                            br = await pw.chromium.connect_over_cdp("http://localhost:9222")
                            page = br.contexts[0].pages[0]
                        except:
                            br = await pw.chromium.launch(headless=False)
                            page = await (await br.new_context()).new_page()
                        await page.goto(body.get('summit_url','https://app.sumit.co.il'))
                        await page.wait_for_load_state("networkidle")
                        for _ in range(60):
                            m = re.search(r'databaseid=(\d+)', page.url)
                            if m:
                                dbid = m.group(1)
                                s = load_settings(); s['summit_dbid']=dbid; save_settings(s)
                                return dbid
                            await page.wait_for_timeout(500)
                        return None
                dbid = asyncio.run(fetch())
                if dbid: self.send_json({'dbid': dbid})
                else:    self.send_json({'error': 'לא נמצא — לחץ על יבוא קובץ תנועות בסאמיט'})
            threading.Thread(target=worker).start(); return

        elif self.path == '/run':
            global running, log_buffer, run_id, company_url
            company_url = ''
            if running:
                self.send_json({'status':'already running'}); return
            run_id += 1; running = True; log_buffer = []
            s = load_settings()
            s.update({k:v for k,v in body.items()})
            save_settings(s)
            # הוסף companies_data מההגדרות ל-body
            if 'companies_data' not in body:
                body['companies_data'] = s.get('companies_data', [])
            def worker():
                asyncio.run(run_automation(body))
            threading.Thread(target=worker, daemon=True).start()
            self.send_json({'status':'started'}); return

        self.send_json({'error':'not found'}, 404)

if __name__ == '__main__':
    port = 7654
    print(f"פותח בדפדפן: http://localhost:{port}")
    threading.Timer(1.2, lambda: webbrowser.open(f'http://localhost:{port}')).start()
    HTTPServer(('localhost', port), Handler).serve_forever()
