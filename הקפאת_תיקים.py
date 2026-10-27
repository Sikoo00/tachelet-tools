#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
כלי הקפאת תיקים — סאמיט
מתחבר לכרום הקיים דרך פורט 9222
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading, asyncio, sys, subprocess, json
from pathlib import Path

COMPANY_ID = "15926449"
SUMMIT_URL = f"https://app.sumit.co.il/books/?companyid={COMPANY_ID}"
CDP_URL    = "http://localhost:9222"
CACHE_FILE = Path.home() / ".summit_tikkim_cache.json"


def ensure_playwright():
    try:
        import playwright
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright", "-q"])
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])

ensure_playwright()
from playwright.async_api import async_playwright


# ── קאש ──────────────────────────────────────────────────────────────────────
def load_cache():
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []

def save_cache(files):
    try:
        CACHE_FILE.write_text(json.dumps(files, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── חילוץ תיקים מ-DOM ────────────────────────────────────────────────────────
JS_EXTRACT = """
() => {
    const SKIP = new Set([
        "כניסה לתיק","הפשרת תיק","הקפאת תיק","מחיקת תיק",
        "עריכה","הוסף","ייצוא","הורד","אשר","ביטול",
        "הקפא","הפשר","כניסה","פתח","X","✓",""
    ]);

    // סוגי עוסק אפשריים בסאמיט — משמש גם לחילוץ העמודה וגם למניעת זיהוי שגוי כשם תיק
    const BUSINESS_TYPES = new Set([
        "עוסק מורשה","עוסק פטור","חברה","עמותה","שותפות",
        'מלכ"ר',"אגודה שיתופית","חברת חוץ","יחיד"
    ]);

    // איתור אינדקס עמודת "מקדמות הבא" לפי טקסט הכותרת עצמה, ולא לפי מיקום קבוע.
    // מיקום קבוע (cells[8]) שביר: אם עמודה נוספת/פחותה מתווספת/מוסרת בטבלה
    // (למשל טאב אחר, פילטר אחר, עמודה חדשה) כל האינדקסים זזים והתא הנשלף
    // כבר לא שייך בכלל לעמודת המקדמות — זה מה שגורם לערכים חסרי משמעות.
    let mikdamotColIdx = -1;
    const headerCells = document.querySelectorAll(
        "table thead th, table thead td, .table thead th, .table thead td"
    );
    headerCells.forEach((th, idx) => {
        const t = th.innerText.trim();
        if (mikdamotColIdx === -1 && t.includes("מקדמות")) {
            mikdamotColIdx = idx;
        }
    });

    const results = [];
    const seen = new Set();
    const rows = document.querySelectorAll("table tbody tr, .table tbody tr");

    rows.forEach(row => {
        let name = "";
        let frozen = false;
        let href = "";
        let mikdamot = "";
        let desekType = "";
        const cells = row.querySelectorAll("td");

        // עמודת "דיווח מקדמות הבא" — קודם לפי כותרת שזוהתה, ורק אם לא
        // נמצאה כותרת כלל נופלים חזרה על ההנחה הישנה (אינדקס 8)
        const idxToUse = mikdamotColIdx !== -1 ? mikdamotColIdx : 8;
        if (cells.length > idxToUse) {
            const cellText = cells[idxToUse].innerText.trim();
            // מחלץ רק תאריך בפורמט XX-XX/XX או XX/XX
            const dateMatch = cellText.match(/\\d{2}-\\d{2}\\/\\d{2}|\\d{2}\\/\\d{2}/);
            if (dateMatch) {
                mikdamot = dateMatch[0];
            }
        }

        cells.forEach(cell => {
            const cellText = cell.innerText.trim();
            if (cellText.includes("הפשרת תיק")) frozen = true;

            if (!href) {
                const link = cell.querySelector("a[href*='databaseid'], a[href*='database']");
                if (link) href = link.href;
            }

            if (!desekType && BUSINESS_TYPES.has(cellText)) {
                desekType = cellText;
            }

            if (!SKIP.has(cellText) && cellText.length > 1
                && !BUSINESS_TYPES.has(cellText)
                && !cellText.match(/^[\\d\\/\\-\\.:\\s]+$/)
                && !cellText.includes("כניסה") && !cellText.includes("הפשרת")
                && !cellText.includes("הקפאת") && !cellText.includes("מחיקת")
                && !cellText.includes("עריכה")) {
                const buttons = cell.querySelectorAll("button, a[role='button'], input[type='button']");
                const btnText = Array.from(buttons).map(b => b.innerText.trim()).join("");
                if (btnText !== cellText && !name) name = cellText;
            }
        });

        if (!href) {
            const link = row.querySelector("a[href*='databaseid'], a[href*='database']");
            if (link) href = link.href;
        }

        if (name && name.length > 1 && !seen.has(name)) {
            seen.add(name);
            results.push({ name, frozen, href, mikdamot, desek_type: desekType });
        }
    });
    return results;
}
"""


# ── שאיבת רשימת תיקים ────────────────────────────────────────────────────────
async def fetch_files_async(log_cb=None):
    def log(msg):
        if log_cb: log_cb(msg)

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            return None, f"לא ניתן להתחבר לכרום.\nודא שכרום פתוח עם סאמיט.\n\nפרטים: {e}"

        page = None
        for ctx in browser.contexts:
            for p in ctx.pages:
                if "sumit" in p.url:
                    page = p
                    break
            if page: break

        if not page:
            page = await browser.contexts[0].new_page()
            await page.goto(SUMMIT_URL)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

        if "books" not in page.url:
            await page.goto(SUMMIT_URL)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

        try:
            tab = page.get_by_text("כל התיקים", exact=False).first
            if await tab.count() > 0:
                await tab.click()
                await page.wait_for_timeout(1500)
        except Exception:
            pass

        log("גולל לטעינת כל התיקים…")
        כל_התיקים = {}

        await page.evaluate("document.documentElement.scrollTop = 0;")
        await page.wait_for_timeout(500)

        for _ in range(60):
            תיקים_עכשיו = await page.evaluate(JS_EXTRACT)
            for ת in (תיקים_עכשיו or []):
                if ת["name"] not in כל_התיקים:
                    כל_התיקים[ת["name"]] = ת

            הגיע_לסוף = await page.evaluate("""
                () => {
                    const before = document.documentElement.scrollTop;
                    document.documentElement.scrollTop += 400;
                    return document.documentElement.scrollTop === before;
                }
            """)
            await page.wait_for_timeout(350)
            log(f"  נמצאו {len(כל_התיקים)} תיקים…")
            if הגיע_לסוף:
                break

        files = list(כל_התיקים.values())
        await browser.close()

        if not files:
            return None, "לא נמצאו תיקים.\nודא שאתה בטאב 'כל התיקים' בסאמיט."

        save_cache(files)
        return files, None


def fetch_files(log_cb=None):
    return asyncio.run(fetch_files_async(log_cb))


# ── הקפאת תיק ────────────────────────────────────────────────────────────────
async def freeze_files_async(files_to_freeze, log_cb=None):
    """
    מקפיא תיקים: פותח כל תיק בטאב → עוד → קישורים נוספים → פעולות מתקדמות → הקפאת תיק
    """
    def log(msg):
        if log_cb: log_cb(msg)

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            return False, f"לא ניתן להתחבר לכרום: {e}"

        errors = []
        for f in files_to_freeze:
            name = f["name"]
            href = f.get("href", "")
            if not href:
                log(f"  ⚠️ {name} — אין קישור, מדלג")
                errors.append(name)
                continue

            log(f"  🔄 מקפיא: {name}…")
            try:
                page = await browser.contexts[0].new_page()
                await page.goto(href)
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(1500)

                # ספור כמה "קישורים נוספים" יש לפני לחיצה על עוד
                kn_before = await page.get_by_text("קישורים נוספים").count()

                # לחץ "עוד" בתפריט העליון
                await page.get_by_text("עוד", exact=True).first.click(timeout=8000)
                await page.wait_for_timeout(700)

                # הדרופדאון של "עוד" הוסיף "קישורים נוספים" — לחץ על הרכיב החדש (האחרון)
                kn_all = page.get_by_text("קישורים נוספים")
                kn_count = await kn_all.count()
                if kn_count > kn_before:
                    # לחץ על הרכיב שנוסף (האחרון ברשימה)
                    await kn_all.nth(kn_count - 1).click(timeout=5000)
                else:
                    # fallback — חפש ספציפית בדרופדאון
                    dropdown = page.locator(
                        '[role="menu"] >> text=קישורים נוספים, '
                        '.dropdown-menu >> text=קישורים נוספים'
                    ).first
                    await dropdown.click(timeout=5000)
                await page.wait_for_timeout(1200)

                # קרא ישירות לפונקציה JS שפותחת את "פעולות מתקדמות"
                # (מהלוג: onclick="OfficeGuy.Modals.ShowHTMLCloneDialog({ Title: 'פעולות מתקדמות', Selector: '#Books-Advanced' })")
                await page.evaluate("""
                    OfficeGuy.Modals.ShowHTMLCloneDialog({
                        Title: 'פעולות מתקדמות',
                        Selector: '#Books-Advanced'
                    });
                """)
                await page.wait_for_timeout(1000)

                # חלץ את ה-databaseid מה-URL הנוכחי של הדף ונווט ישירות ל-freeze
                current_url = page.url
                import re as _re
                m = _re.search(r'databaseid=(\d+)', current_url)
                if not m:
                    # נסה לחלץ מהדום
                    dbid = await page.evaluate("""
                        () => {
                            const a = document.querySelector('a[href*="freeze"][href*="databaseid"]');
                            if (!a) return null;
                            const m = a.href.match(/databaseid=(\\d+)/);
                            return m ? m[1] : null;
                        }
                    """)
                else:
                    dbid = m.group(1)

                if not dbid:
                    log(f"  ❌ {name} — לא נמצא databaseid")
                    errors.append(name)
                    continue

                freeze_url = f"https://app.sumit.co.il/books/databases/freeze/?databaseid={dbid}"
                log(f"  → נווט להקפאה: {freeze_url}")
                await page.goto(freeze_url)
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(1000)

                # חלץ קוד אימות מהטקסט: "יש להקליד את קוד האימות XXXXXX"
                code = await page.evaluate("""
                    () => {
                        const text = document.body.innerText;
                        const m = text.match(/קוד האימות\\s+([A-Z0-9]{4,10})/);
                        return m ? m[1] : null;
                    }
                """)

                if not code:
                    log(f"  ❌ {name} — לא נמצא קוד אימות בעמוד")
                    errors.append(name)
                    continue

                log(f"  → קוד אימות: {code}")

                # הכנס את הקוד בשדה
                code_input = page.locator('input[name*="code"], input[id*="code"], input[placeholder*="קוד"], input[type="text"]').first
                await code_input.wait_for(state="visible", timeout=5000)
                await code_input.fill(code)
                await page.wait_for_timeout(400)

                # לחץ כפתור "הקפאת תיק" האדום
                await page.click('button:has-text("הקפאת תיק"), input[value*="הקפאת תיק"], a:has-text("הקפאת תיק")', timeout=5000)
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(1000)

                log(f"  ✅ {name} — הוקפא בהצלחה!")
                # הטאב נשאר פתוח
            except Exception as e:
                log(f"  ❌ {name} — שגיאה: {e}")
                errors.append(name)

        await browser.close()
        return len(errors) == 0, errors


# ── הפשרת תיק ────────────────────────────────────────────────────────────────
async def unfreeze_files_async(files_to_unfreeze, log_cb=None):
    """
    מפשיר תיקים: פותח טאב חדש לכל תיק ונווט ל-URL ההפשרה
    """
    import re as _re

    def log(msg):
        if log_cb: log_cb(msg)

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            return False, f"לא ניתן להתחבר לכרום: {e}"

        errors = []

        for f in files_to_unfreeze:
            name = f["name"]
            href = f.get("href", "")
            log(f"  🔄 מפשיר: {name}…")

            try:
                # חלץ databaseid מה-href של התיק
                dbid = None
                if href:
                    m = _re.search(r'databaseid=(\d+)', href)
                    if m:
                        dbid = m.group(1)

                if not dbid:
                    # נסה לחפש בטאב הפתוח של כל התיקים
                    books_page = None
                    for ctx in browser.contexts:
                        for p in ctx.pages:
                            if "sumit" in p.url and "books" in p.url and "database" not in p.url:
                                books_page = p
                                break
                        if books_page: break

                    if books_page:
                        dbid = await books_page.evaluate(f"""
                            () => {{
                                const rows = document.querySelectorAll('tr');
                                for (const row of rows) {{
                                    if (row.innerText.includes('{name}')) {{
                                        const a = row.querySelector('a[href*="databaseid"]');
                                        if (a) {{
                                            const m = a.href.match(/databaseid=(\\d+)/);
                                            return m ? m[1] : null;
                                        }}
                                    }}
                                }}
                                return null;
                            }}
                        """)

                if not dbid:
                    log(f"  ❌ {name} — לא נמצא databaseid")
                    errors.append(name)
                    continue

                # פתח טאב חדש ונווט ל-URL ההפשרה
                unfreeze_url = f"https://app.sumit.co.il/books/databases/unfreeze/?databaseid={dbid}"
                log(f"  → נווט להפשרה: {unfreeze_url}")
                tab = await browser.contexts[0].new_page()
                await tab.goto(unfreeze_url)
                await tab.wait_for_load_state("networkidle")
                await tab.wait_for_timeout(1000)

                # לחץ "אישור" בחלון האישור שנפתח
                await tab.wait_for_selector(
                    'button:has-text("אישור"), a:has-text("אישור")',
                    state="visible", timeout=8000)
                await tab.click(
                    'button:has-text("אישור"), a:has-text("אישור")',
                    timeout=5000)
                await tab.wait_for_load_state("networkidle")
                await tab.wait_for_timeout(1000)

                log(f"  ✅ {name} — הופשר בהצלחה!")
                # הטאב נשאר פתוח

            except Exception as e:
                log(f"  ❌ {name} — שגיאה: {e}")
                errors.append(name)

        await browser.close()
        return len(errors) == 0, errors


def run_action_thread(coro, log_cb, done_cb):
    async def run():
        return await coro
    result = asyncio.run(run())
    if done_cb:
        done_cb(result)


# ── ממשק גרפי ─────────────────────────────────────────────────────────────────
COLORS = {
    "header_bg":   "#1a4fa0",
    "bar_bg":      "#edf2fb",
    "col_bg":      "#d4e0f5",
    "row_even":    "#ffffff",
    "row_odd":     "#f3f7ff",
    "row_hover":   "#e8f0fe",
    "frozen_fg":   "#1e40af",
    "frozen_bg":   "#dbeafe",
    "active_fg":   "#166534",
    "active_bg":   "#dcfce7",
    "name_fg":     "#1a2a4a",
    "freeze_btn":  "#1a4fa0",
    "unfreeze_btn":"#b45309",
    "sort_active": "#0f3480",
    "sep":         "#c8d8f0",
}

# צבעי תגית לפי סוג עוסק
DESEK_STYLES = {
    "חברה":            ("#6d28d9", "#ede9fe"),
    "עוסק מורשה":      ("#0369a1", "#e0f2fe"),
    "עוסק פטור":       ("#b45309", "#fef3c7"),
    "עמותה":           ("#0f766e", "#ccfbf1"),
    'מלכ"ר':          ("#0f766e", "#ccfbf1"),
    "שותפות":          ("#a21caf", "#fae8ff"),
    "אגודה שיתופית":   ("#a21caf", "#fae8ff"),
    "חברת חוץ":        ("#6d28d9", "#ede9fe"),
    "יחיד":            ("#0369a1", "#e0f2fe"),
}
DESEK_DEFAULT_STYLE = ("#777777", "#eeeeee")

def desek_style(desek_type):
    return DESEK_STYLES.get(desek_type, DESEK_DEFAULT_STYLE)


class ProgressDialog(tk.Toplevel):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.title(title)
        self.geometry("480x320")
        self.configure(bg=COLORS["bar_bg"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        tk.Label(self, text=title, font=("Arial", 13, "bold"),
                 bg=COLORS["header_bg"], fg="white", pady=10).pack(fill="x")

        self.txt = tk.Text(self, font=("Courier New", 10), bg="#1a2a4a", fg="#a0e0a0",
                           relief="flat", wrap="word", padx=10, pady=8)
        self.txt.pack(fill="both", expand=True, padx=12, pady=10)
        self.txt.config(state="disabled")

        self.btn_close = tk.Button(self, text="סגור", font=("Arial", 11),
                                   bg="#2563eb", fg="white", relief="flat",
                                   padx=20, pady=6, cursor="hand2",
                                   state="disabled", command=self.destroy)
        self.btn_close.pack(pady=(0, 12))

    def append(self, msg):
        self.txt.config(state="normal")
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")
        self.txt.config(state="disabled")

    def done(self):
        self.btn_close.config(state="normal")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("כלי הקפאת תיקים — סאמיט")
        self.geometry("760x720")
        self.minsize(620, 500)
        self.configure(bg=COLORS["bar_bg"])
        self.files   = []
        self.display = []
        self.check_vars = []
        self.selected_names = set()
        self._last_clicked_index = None
        self._shift_held = False
        self.sort_col = "name"
        self.sort_asc = True
        self._build()
        # טען מקאש בפתיחה
        cached = load_cache()
        if cached:
            self.files = cached
            self._apply_sort()
            n_a = sum(1 for f in self.files if not f.get("frozen"))
            n_f = sum(1 for f in self.files if f.get("frozen"))
            self.lbl_status.config(
                text=f"נטענו {len(self.files)} תיקים (קאש)  |  ✅ {n_a}  |  ❄️ {n_f}",
                fg="#555")

    def _build(self):
        # כותרת
        hdr = tk.Frame(self, bg=COLORS["header_bg"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="📂  כלי הקפאת תיקים — סאמיט",
                 font=("Arial", 15, "bold"), bg=COLORS["header_bg"],
                 fg="white", pady=13).pack()

        # סרגל
        bar = tk.Frame(self, bg=COLORS["bar_bg"], pady=8)
        bar.pack(fill="x")
        self.btn_load = tk.Button(bar, text="🔄  טען תיקים",
                                  font=("Arial", 11, "bold"),
                                  bg="#2563eb", fg="white", relief="flat",
                                  padx=16, pady=7, cursor="hand2",
                                  command=self._load_thread)
        self.btn_load.pack(side="right", padx=12)
        self.lbl_status = tk.Label(bar, text="לחץ 'טען תיקים' לרענון",
                                   font=("Arial", 10), bg=COLORS["bar_bg"], fg="#555")
        self.lbl_status.pack(side="right", padx=4)

        # כותרות עמודות
        col_hdr = tk.Frame(self, bg=COLORS["col_bg"])
        col_hdr.pack(fill="x")

        col_hdr = tk.Frame(self, bg=COLORS["col_bg"], height=36)
        col_hdr.pack(fill="x")
        col_hdr.pack_propagate(False)

        self.var_all = tk.BooleanVar(value=False)
        tk.Checkbutton(col_hdr, text="הכל", variable=self.var_all,
                       bg=COLORS["col_bg"], font=("Arial", 10),
                       cursor="hand2", command=self._toggle_all
                       ).place(relx=0.99, rely=0.5, anchor="e")

        self.btn_col_selected = tk.Button(
            col_hdr, text="☑", font=("Arial", 11, "bold"),
            bg=COLORS["col_bg"], fg="#1a3060", relief="flat",
            cursor="hand2", command=lambda: self._sort("selected"))
        self.btn_col_selected.place(relx=0.93, rely=0.5, anchor="center")

        self.btn_col_name = tk.Button(
            col_hdr, text="שם תיק ▲", font=("Arial", 10, "bold"),
            bg=COLORS["col_bg"], fg=COLORS["sort_active"], relief="flat",
            cursor="hand2", command=lambda: self._sort("name"))
        self.btn_col_name.place(relx=0.88, rely=0.5, anchor="e")

        self.btn_col_desek = tk.Button(
            col_hdr, text="סוג עוסק", font=("Arial", 10, "bold"),
            bg=COLORS["col_bg"], fg="#1a3060", relief="flat",
            cursor="hand2", command=lambda: self._sort("desek_type"))
        self.btn_col_desek.place(relx=0.62, rely=0.5, anchor="center")

        self.btn_col_mikdamot = tk.Button(
            col_hdr, text="מקדמות הבא", font=("Arial", 10, "bold"),
            bg=COLORS["col_bg"], fg="#1a3060", relief="flat",
            cursor="hand2", command=lambda: self._sort("mikdamot"))
        self.btn_col_mikdamot.place(relx=0.38, rely=0.5, anchor="center")

        self.btn_col_status = tk.Button(
            col_hdr, text="סטטוס", font=("Arial", 10, "bold"),
            bg=COLORS["col_bg"], fg="#1a3060", relief="flat",
            cursor="hand2", command=lambda: self._sort("status"))
        self.btn_col_status.place(relx=0.13, rely=0.5, anchor="center")

        tk.Frame(self, bg=COLORS["sep"], height=1).pack(fill="x")

        # רשימה
        outer = tk.Frame(self, bg="white")
        outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(outer, bg="white", highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg="white")
        self.inner.bind("<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.bind("<Configure>",
            lambda e: self.canvas.itemconfig(self._win, width=e.width))
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.bind_all("<MouseWheel>",
            lambda e: self.canvas.yview_scroll(-1*(e.delta//120), "units"))

        tk.Frame(self, bg=COLORS["sep"], height=1).pack(fill="x")

        # כפתורי פעולה
        btn_bar = tk.Frame(self, bg=COLORS["bar_bg"], pady=10)
        btn_bar.pack(fill="x")
        self.lbl_selected = tk.Label(btn_bar, text="",
                                     font=("Arial", 10), bg=COLORS["bar_bg"], fg="#555")
        self.lbl_selected.pack(side="left", padx=14)
        self.btn_freeze = tk.Button(btn_bar, text="❄️  הקפא נבחרים",
                                    font=("Arial", 12, "bold"),
                                    bg=COLORS["freeze_btn"], fg="white", relief="flat",
                                    padx=18, pady=9, cursor="hand2",
                                    command=lambda: self._action("freeze"),
                                    state="disabled")
        self.btn_freeze.pack(side="right", padx=10)
        self.btn_unfreeze = tk.Button(btn_bar, text="🔥  הפשר נבחרים",
                                      font=("Arial", 12, "bold"),
                                      bg=COLORS["unfreeze_btn"], fg="white", relief="flat",
                                      padx=18, pady=9, cursor="hand2",
                                      command=lambda: self._action("unfreeze"),
                                      state="disabled")
        self.btn_unfreeze.pack(side="right", padx=4)

        self.lbl_log = tk.Label(self, text="", font=("Arial", 9),
                                bg=COLORS["bar_bg"], fg="#888", pady=2)
        self.lbl_log.pack(fill="x", padx=12)

    # ── טעינה ──────────────────────────────────────────────────────────────────
    def _load_thread(self):
        self.btn_load.config(state="disabled", text="טוען…")
        self.lbl_status.config(text="מתחבר לסאמיט…", fg="#555")
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        def log(msg):
            self.after(0, lambda m=msg: self.lbl_log.config(text=m))
        files, err = fetch_files(log_cb=log)
        self.after(0, lambda: self._on_loaded(files, err))

    def _on_loaded(self, files, err):
        self.btn_load.config(state="normal", text="🔄  טען תיקים")
        self.lbl_log.config(text="")
        if err:
            self.lbl_status.config(text="שגיאה", fg="red")
            messagebox.showerror("שגיאה", err)
            return
        self.files = files or []
        self.sort_col = "name"
        self.sort_asc = True
        self._apply_sort()
        n_a = sum(1 for f in self.files if not f.get("frozen"))
        n_f = sum(1 for f in self.files if f.get("frozen"))
        self.lbl_status.config(
            text=f"נטענו {len(self.files)} תיקים  |  ✅ פעילים: {n_a}  |  ❄️ קפואים: {n_f}",
            fg="#1a4fa0")

    # ── מיון ───────────────────────────────────────────────────────────────────
    def _sort(self, col):
        if self.sort_col == col:
            self.sort_asc = not self.sort_asc
        else:
            self.sort_col = col
            self.sort_asc = True
        self._apply_sort()

    def _apply_sort(self):
        asc = self.sort_asc
        if self.sort_col == "name":
            self.display = sorted(self.files, key=lambda f: f["name"], reverse=not asc)
        elif self.sort_col == "status":
            self.display = sorted(self.files, key=lambda f: f.get("frozen", False), reverse=not asc)
        elif self.sort_col == "mikdamot":
            self.display = sorted(self.files, key=lambda f: f.get("mikdamot", ""), reverse=not asc)
        elif self.sort_col == "desek_type":
            self.display = sorted(self.files, key=lambda f: f.get("desek_type", ""), reverse=not asc)
        elif self.sort_col == "selected":
            self.display = sorted(self.files, key=lambda f: f["name"] in self.selected_names, reverse=asc)
        else:
            self.display = list(self.files)

        arrow = "▲" if asc else "▼"
        self.btn_col_name.config(
            text=f"שם תיק {arrow}" if self.sort_col == "name" else "שם תיק",
            fg=COLORS["sort_active"] if self.sort_col == "name" else "#1a3060")
        self.btn_col_status.config(
            text=f"סטטוס {arrow}" if self.sort_col == "status" else "סטטוס",
            fg=COLORS["sort_active"] if self.sort_col == "status" else "#1a3060")
        self.btn_col_mikdamot.config(
            text=f"מקדמות הבא {arrow}" if self.sort_col == "mikdamot" else "מקדמות הבא",
            fg=COLORS["sort_active"] if self.sort_col == "mikdamot" else "#1a3060")
        self.btn_col_desek.config(
            text=f"סוג עוסק {arrow}" if self.sort_col == "desek_type" else "סוג עוסק",
            fg=COLORS["sort_active"] if self.sort_col == "desek_type" else "#1a3060")
        self.btn_col_selected.config(
            text=f"☑ {arrow}" if self.sort_col == "selected" else "☑",
            fg=COLORS["sort_active"] if self.sort_col == "selected" else "#1a3060")
        self._render()

    # ── רינדור ─────────────────────────────────────────────────────────────────
    def _render(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.check_vars = []

        if not self.display:
            tk.Label(self.inner, text="לא נמצאו תיקים",
                     font=("Arial", 12), bg="white", fg="#aaa").pack(pady=40)
            return

        for i, f in enumerate(self.display):
            bg = COLORS["row_even"] if i % 2 == 0 else COLORS["row_odd"]
            row = tk.Frame(self.inner, bg=bg, height=40)
            row.pack(fill="x")
            row.pack_propagate(False)

            var = tk.BooleanVar(value=(f["name"] in self.selected_names))
            self.check_vars.append(var)

            # checkbox — ימין
            chk = tk.Checkbutton(row, variable=var, bg=bg, activebackground=bg,
                           command=lambda n=f["name"], v=var, idx=i: self._on_toggle(n, v, idx))
            chk.place(relx=0.99, rely=0.5, anchor="e")
            chk.bind("<Button-1>", self._remember_shift, add="+")

            # שם תיק — לפני ריבוע הבחירה
            tk.Label(row, text=f["name"], font=("Arial", 11, "bold"),
                     bg=bg, fg=COLORS["name_fg"]
                     ).place(relx=0.88, rely=0.5, anchor="e")

            # סוג עוסק — תגית צבעונית
            desek_type = f.get("desek_type", "")
            if desek_type:
                d_fg, d_bg = desek_style(desek_type)
                tk.Label(row, text=f"● {desek_type}", font=("Arial", 9, "bold"),
                         bg=d_bg, fg=d_fg, padx=7, pady=1
                         ).place(relx=0.62, rely=0.5, anchor="center")
            else:
                tk.Label(row, text="—", font=("Arial", 10),
                         bg=bg, fg="#ccc"
                         ).place(relx=0.62, rely=0.5, anchor="center")

            # מקדמות הבא — מרכז
            mikdamot = f.get("mikdamot", "")
            tk.Label(row, text=mikdamot if mikdamot else "—",
                     font=("Arial", 10),
                     bg=bg, fg="#1a4fa0" if mikdamot else "#ccc"
                     ).place(relx=0.38, rely=0.5, anchor="center")

            # סטטוס
            if f.get("frozen"):
                s_txt, s_fg, s_bg = "❄️ קפוא", COLORS["frozen_fg"], COLORS["frozen_bg"]
                s_font = ("Arial", 10, "bold")
            else:
                s_txt, s_fg, s_bg = "✅ פעיל", COLORS["active_fg"], COLORS["active_bg"]
                s_font = ("Arial", 10)
            tk.Label(row, text=s_txt, font=s_font, bg=s_bg, fg=s_fg, padx=7, pady=1
                     ).place(relx=0.13, rely=0.5, anchor="center")

            tk.Frame(self.inner, bg=COLORS["sep"], height=1).pack(fill="x")
            row.bind("<Enter>", lambda e, r=row: self._hover(r, True))
            row.bind("<Leave>", lambda e, r=row, b=bg: self._hover(r, False, b))

        self._on_check()

    def _hover(self, row, on, orig="white"):
        c = COLORS["row_hover"] if on else orig
        try:
            row.configure(bg=c)
            for w in row.winfo_children():
                try: w.configure(bg=c)
                except: pass
        except: pass

    def _toggle_all(self):
        v = self.var_all.get()
        for f, var in zip(self.display, self.check_vars):
            var.set(v)
            if v:
                self.selected_names.add(f["name"])
            else:
                self.selected_names.discard(f["name"])
        self._on_check()

    def _remember_shift(self, event):
        # נלכד בלחיצה עצמה (Button-1), לפני שה-command של תיבת הסימון רץ,
        # כדי שנדע אם SHIFT היה לחוץ ברגע הקליק
        self._shift_held = bool(event.state & 0x0001)

    def _on_toggle(self, name, var, index):
        new_val = var.get()
        shift = getattr(self, "_shift_held", False)
        last = getattr(self, "_last_clicked_index", None)

        if shift and last is not None and 0 <= last < len(self.display):
            lo, hi = sorted((last, index))
            for i in range(lo, hi + 1):
                f = self.display[i]
                self.check_vars[i].set(new_val)
                if new_val:
                    self.selected_names.add(f["name"])
                else:
                    self.selected_names.discard(f["name"])
        else:
            if new_val:
                self.selected_names.add(name)
            else:
                self.selected_names.discard(name)

        self._last_clicked_index = index
        self._on_check()

    def _on_check(self):
        count = sum(1 for v in self.check_vars if v.get())
        s = "normal" if count > 0 else "disabled"
        self.btn_freeze.config(state=s)
        self.btn_unfreeze.config(state=s)
        self.lbl_selected.config(text=f"{count} תיקים נבחרו" if count else "")

    # ── הקפא / הפשר ────────────────────────────────────────────────────────────
    def _action(self, action):
        selected = [self.display[i] for i, v in enumerate(self.check_vars) if v.get()]
        if not selected:
            return
        action_he = "הקפאת" if action == "freeze" else "הפשרת"
        names = "\n• ".join(f["name"] for f in selected)
        if not messagebox.askyesno("אישור", f"{action_he} {len(selected)} תיקים:\n• {names}\n\nלהמשיך?"):
            return

        dlg = ProgressDialog(self, f"{'❄️ הקפאת' if action=='freeze' else '🔥 הפשרת'} תיקים")
        dlg.append(f"מתחיל {action_he} {len(selected)} תיקים…\n")

        def log(msg):
            self.after(0, lambda m=msg: dlg.append(m))

        def done(result):
            ok, errors = result
            if ok:
                self.after(0, lambda: dlg.append("\n✅ הושלם בהצלחה!"))
            else:
                self.after(0, lambda: dlg.append(f"\n⚠️ שגיאות ב: {', '.join(errors)}"))
            self.after(0, dlg.done)

        if action == "freeze":
            coro = freeze_files_async(selected, log_cb=log)
        else:
            coro = unfreeze_files_async(selected, log_cb=log)

        threading.Thread(
            target=lambda: done(asyncio.run(coro)),
            daemon=True
        ).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
