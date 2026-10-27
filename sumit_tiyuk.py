# -*- coding: utf-8 -*-
"""
כלי תיוק אוטומטי לסאמיט
נכנס לעמוד "קליטת חומרים" שבו מופיעים רק תיקים לתיוק
"""

import time
import threading
import queue
import tkinter as tk
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

COMPANY_ID = "15926449"
SUMIT_URL = f"https://app.sumit.co.il/books/?companyid={COMPANY_ID}"
# עמוד קליטת חומרים — מציג רק תיקים שיש בהם קבצים לתיוק
INTAKE_URL = f"https://app.sumit.co.il/books/fileexpenses/?companyid={COMPANY_ID}"


# ─────────────────────────────────────────────────────────────────────────────
# חיבור לכרום
# ─────────────────────────────────────────────────────────────────────────────
def חבר_לכרום():
    אפשרויות = Options()
    אפשרויות.add_experimental_option("debuggerAddress", "localhost:9222")
    return webdriver.Chrome(options=אפשרויות)


# ─────────────────────────────────────────────────────────────────────────────
# ניווט
# ─────────────────────────────────────────────────────────────────────────────
def עבור_לקליטת_חומרים(נהג, חלון=None):
    """
    נווט לעמוד קליטת חומרים — לוחצים על הלשונית בדף הראשי.
    """
    if חלון:
        חלון.כתוב("עובר לעמוד קליטת חומרים…")

    # קודם עובר לדף הראשי
    נהג.get(SUMIT_URL)
    for _ in range(30):
        time.sleep(0.5)
        שורות = נהג.execute_script(
            "return document.querySelectorAll('table tr').length;"
        ) or 0
        if שורות > 3:
            break
    time.sleep(1)

    # לוחץ על לשונית "קליטת חומרים" — דווקא בתוך אזור הלשוניות של הדף, לא בתפריט הראשי
    שם_לשונית = נהג.execute_script("""
        // חפש את אזור הלשוניות (tabs bar) — בדרך כלל .nav, .tabs, ul.nav, או div עם [role=tablist]
        var tabAreas = document.querySelectorAll(
            '[role="tablist"], .nav.nav-tabs, .tabs-container, .page-tabs, ul.nav, .og-tabs'
        );
        for (var a = 0; a < tabAreas.length; a++) {
            var items = tabAreas[a].querySelectorAll('a, button, li, [role="tab"]');
            for (var i = 0; i < items.length; i++) {
                var t = items[i].textContent.trim();
                if (t.includes('קליטת חומרים') || t.includes('קליטת תומרים')) {
                    items[i].click();
                    return t;
                }
            }
        }
        // גיבוי — חפש בכל הדף אבל וודא שהאלמנט אינו בתפריט הניווט הראשי
        var all = document.querySelectorAll('a, button, [role="tab"]');
        for (var i = 0; i < all.length; i++) {
            var t = all[i].textContent.trim();
            if ((t.includes('קליטת חומרים') || t.includes('קליטת תומרים')) &&
                !all[i].closest('nav') && !all[i].closest('header') &&
                !all[i].closest('.main-nav') && !all[i].closest('.top-nav')) {
                all[i].click();
                return t;
            }
        }
        return null;
    """)

    if שם_לשונית and חלון:
        חלון.כתוב(f"  נלחצה לשונית: {שם_לשונית}")
    elif חלון:
        חלון.כתוב("  לא נמצאה לשונית קליטת חומרים — ממשיך בדף הראשי")

    # מחכה לטעינת הלשונית — בודק שהטבלה התחלפה
    time.sleep(2)
    for _ in range(30):
        יש_קישורים = נהג.execute_script("""
            var links = document.querySelectorAll('table tr td a');
            for (var i = 0; i < links.length; i++) {
                var h = links[i].href || '';
                if (h.includes('fileexpenses') || h.includes('databaseid')) return true;
            }
            return false;
        """)
        if יש_קישורים:
            break
        time.sleep(0.5)

    if חלון:
        url_now = נהג.current_url
        חלון.כתוב(f"  URL נוכחי: {url_now}")
        שורות = נהג.execute_script("return document.querySelectorAll('table tr').length;") or 0
        חלון.כתוב(f"  שורות בטבלה: {שורות}")
        # דיבאג — הצג את כל ה-href בטבלה
        קישורים = נהג.execute_script("""
            var hrefs = [];
            var links = document.querySelectorAll('table tr td a');
            for (var i = 0; i < Math.min(links.length, 5); i++) {
                hrefs.push(links[i].href + ' [' + links[i].textContent.trim() + ']');
            }
            return hrefs;
        """) or []
        for k in קישורים:
            חלון.כתוב(f"  קישור: {k}")
    time.sleep(0.5)


def חזור_לראשי(נהג):
    """חזרה לעמוד ראשי — לאחר גמר תיק."""
    try:
        נהג.execute_script("""
            var link = document.querySelector(
                "a[href*='/books/?companyid='], a[href*='books/?companyid=']"
            );
            if (link) link.click();
        """)
        time.sleep(1)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# מצא תיקים בעמוד קליטת חומרים
# ─────────────────────────────────────────────────────────────────────────────
def מצא_תיקים_בקליטה(נהג, חלון=None):
    """
    אוסף את כל התיקים עם קבצים לקליטה — עם Selenium ישירות (לא JS).
    """
    import re as _re
    from selenium.webdriver.common.by import By

    כל_התיקים = {}  # dbid -> {שם, כמות, url}

    נהג.execute_script("document.documentElement.scrollTop = 0;")
    time.sleep(0.8)

    for _ in range(80):
        # מצא כל קישורים בטבלה
        try:
            קישורים = נהג.find_elements(By.CSS_SELECTOR, "table tr td a")
        except Exception:
            קישורים = []

        # בנה מפה: databaseid -> שם (מקישורי database/?)
        name_map = {}
        for a in קישורים:
            try:
                href = a.get_attribute("href") or ""
                txt = (a.text or "").strip()
                m = _re.search(r"databaseid=(\d+)", href)
                if m and txt and len(txt) > 1 and not txt.isdigit():
                    name_map[m.group(1)] = txt
            except Exception:
                continue

        # מצא fileexpenses עם מספר > 0
        for a in קישורים:
            try:
                href = a.get_attribute("href") or ""
                if "fileexpenses" not in href:
                    continue
                txt = (a.text or "").strip()
                if not txt.isdigit():
                    continue
                n = int(txt)
                if n <= 0:
                    continue
                m = _re.search(r"databaseid=(\d+)", href)
                if not m:
                    continue
                dbid = m.group(1)
                if dbid in כל_התיקים:
                    continue
                שם = name_map.get(dbid, f"תיק {dbid}")
                כל_התיקים[dbid] = {"שם": שם, "כמות": n, "url": href}
                if חלון:
                    חלון.כתוב(f"  נמצא: {שם} ({n} קבצים) — {href}")
            except Exception:
                continue

        # גלול מטה
        הגיע_לסוף = נהג.execute_script("""
            var before = document.documentElement.scrollTop;
            document.documentElement.scrollTop += 350;
            return document.documentElement.scrollTop === before;
        """)
        time.sleep(0.35)
        if הגיע_לסוף:
            break

    רשימה = list(כל_התיקים.values())
    if חלון:
        חלון.כתוב(f"סה\"כ נמצאו {len(רשימה)} תיקים")
    return רשימה


# ─────────────────────────────────────────────────────────────────────────────
# בדיקת חלונות מיוחדים
# ─────────────────────────────────────────────────────────────────────────────
def בדוק_חלונות_מיוחדים(נהג, חלון_התקדמות=None, מצב_כפילות=None, סוף_תיק=False):
    """
    סוף_תיק=True  → לוחצים אישור (עוברים לתיק הבא אוטומטית)
    סוף_תיק=False → לוחצים ביטול (נשארים / חוזרים ידנית)
    """
    if מצב_כפילות is None:
        מצב_כפילות = [False]

    def ל(msg):
        if חלון_התקדמות:
            חלון_התקדמות.כתוב(msg)

    try:
        סוג = נהג.execute_script("""
            var modal = document.querySelector('.modal-dialog');
            if (!modal || modal.style.display === 'none') return null;
            var text = modal.textContent || '';
            if (text.includes('סוף הקבצים'))          return 'סוף_קבצים';
            if (text.includes('חשד לכפילות'))          return 'כפילות';
            if (text.includes('מחיקת תנועה') ||
                text.includes('למחוק את התנועה'))       return 'מחיקה';
            return null;
        """)

        if סוג == 'סוף_קבצים':
            # חלץ שם התיק הבא מתוך טקסט החלון (מופיע לפני "יורשאל רז" / לפני פסיק)
            שם_תיק_הבא = None
            try:
                שם_תיק_הבא = נהג.execute_script("""
                    var modal = document.querySelector('.modal-dialog');
                    if (!modal) return null;
                    var text = modal.textContent || '';
                    // דוגמה: "...לתיק הבא. מנצור שקד"
                    var m = text.match(/לתיק הבא[.,]\\s*([^.\\n]+)/);
                    return m ? m[1].trim() : null;
                """)
            except Exception:
                pass
            if שם_תיק_הבא:
                ל(f"  שם התיק הבא: {שם_תיק_הבא}")

            if סוף_תיק:
                ל("  סוף הקבצים — לוחץ אישור (ממשיך לתיק הבא)")
                נהג.execute_script("""
                    var modal = document.querySelector('.modal-dialog');
                    if (modal) {
                        var btns = [...modal.querySelectorAll('a[class*="og-button"]')];
                        var ok = btns.find(b => b.textContent.trim() === 'אישור' || b.textContent.trim() === 'OK');
                        if (!ok) ok = btns.find(b => !b.textContent.includes('ביטול'));
                        if (ok) ok.click();
                    }
                """)
            else:
                ל("  סוף הקבצים — לוחץ ביטול")
                נהג.execute_script("""
                    var modal = document.querySelector('.modal-dialog');
                    if (modal) {
                        var btns = [...modal.querySelectorAll('a[class*="og-button"]')];
                        var cancel = btns.find(b => b.textContent.trim() === 'ביטול');
                        if (cancel) cancel.click();
                    }
                """)
            time.sleep(2)
            return ('סוף_קבצים', שם_תיק_הבא)

        elif סוג == 'כפילות':
            ל("  חשד לכפילות — אישרתי")
            נהג.execute_script("""
                var modal = document.querySelector('.modal-dialog');
                if (modal) {
                    var btn = modal.querySelector(
                        '.modal-buttons input[class*="og-button-color-submit"],' +
                        'a[class*="og-button-color-submit"]');
                    if (btn) btn.click();
                }
            """)
            מצב_כפילות[0] = True
            time.sleep(2)

            # בדוק אם אחרי אישור כפילות הופיע "סוף הקבצים"
            סוג2 = נהג.execute_script("""
                var modal = document.querySelector('.modal-dialog');
                if (!modal || modal.style.display==='none') return null;
                return modal.textContent.includes('סוף הקבצים') ? 'סוף_קבצים' : null;
            """)
            if סוג2 == 'סוף_קבצים':
                ל("  סוף הקבצים אחרי כפילות — לוחץ ביטול קודם")
                נהג.execute_script("""
                    var modal = document.querySelector('.modal-dialog');
                    if (modal) {
                        var btns = [...modal.querySelectorAll('a[class*="og-button"]')];
                        var cancel = btns.find(b => b.textContent.trim() === 'ביטול');
                        if (cancel) cancel.click();
                    }
                """)
                time.sleep(1.5)

            # לחץ מחיקה
            נהג.execute_script("""
                var deleteBtn = document.querySelector('a[class*="og-button-color-danger"]');
                if (deleteBtn) deleteBtn.click();
            """)
            time.sleep(2)
            # אשר מחיקה
            נהג.execute_script("""
                var modal = document.querySelector('.modal-dialog');
                if (modal) {
                    var btn = modal.querySelector(
                        '.modal-buttons input[class*="og-button-color-submit"],' +
                        'a[class*="og-button-color-submit"]');
                    if (btn) btn.click();
                }
            """)
            ל("  מחיקה — אישרתי")
            מצב_כפילות[0] = False
            time.sleep(3)
            return 'מחיקה'

        elif סוג == 'מחיקה':
            ל("  מחיקת תנועה — אישרתי")
            נהג.execute_script("""
                var modal = document.querySelector('.modal-dialog');
                if (modal) {
                    var btn = modal.querySelector(
                        '.modal-buttons input[class*="og-button-color-submit"],' +
                        'a[class*="og-button-color-submit"]');
                    if (btn) btn.click();
                }
            """)
            מצב_כפילות[0] = False
            time.sleep(3)
            return 'מחיקה'

    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# כמות קבצים ממתינים לפי כותרת הדף
# ─────────────────────────────────────────────────────────────────────────────
def קרא_כמות_ממתינים(נהג):
    """קורא את המספר בסוגריים מכותרת 'X קבצים ממתינים'."""
    try:
        כמות = נהג.execute_script("""
            var text = document.body.innerText;
            var m = text.match(/(\\d+)\\s*קבצים\\s*ממתינים/);
            return m ? parseInt(m[1]) : null;
        """)
        return כמות
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# בדיקת שדות ושמירה
# ─────────────────────────────────────────────────────────────────────────────
def האם_ניתן_לשמור(נהג):
    try:
        חסרים = נהג.execute_script("""
            var missing = [];
            var f = {
                date:    document.querySelector('input[name="ReferenceDate"]'),
                ref:     document.querySelector('input[name="Reference1"]'),
                amount:  document.querySelector('input[name="AmountILS"]'),
                debit:   document.querySelector('input[name="DebitAccount"]'),
                credit:  document.querySelector('input[name="CreditAccount"]'),
                cust:    document.querySelector('input[name="CustomerSupplierAccount"]')
            };
            if (f.date   && !f.date.value.trim())   missing.push('תאריך');
            if (f.ref    && !f.ref.value.trim())     missing.push('אסמכתא');
            if (f.amount && !f.amount.value.trim())  missing.push('סכום');
            var hasAccount = (f.debit  && f.debit.value.trim()) ||
                             (f.credit && f.credit.value.trim()) ||
                             (f.cust   && f.cust.value.trim());
            if (!hasAccount) missing.push('חשבון חובה/זכות');
            return missing;
        """)
        חסרים = חסרים or []
        return len(חסרים) == 0, חסרים
    except Exception:
        return True, []


def לחץ_שמירה(נהג):
    try:
        ok = נהג.execute_script("""
            var btn = [...document.querySelectorAll('a[class*="og-button"]')]
                .find(b => b.textContent.trim() === 'שמירה');
            if (!btn) return false;
            btn.removeAttribute('disabled');
            btn.scrollIntoView({block:'center'});
            btn.click();
            return true;
        """)
        if ok:
            time.sleep(3)
            return True
    except Exception:
        pass
    return False


def לחץ_דילוג(נהג):
    try:
        כפתור = נהג.execute_script("""
            return [...document.querySelectorAll('a[class*="og-button"]')]
                .find(b => b.className.includes('rendernexttransaction')
                      && b.className.includes('tableicon'));
        """)
        if כפתור:
            נהג.execute_script("arguments[0].scrollIntoView({block:'center'});", כפתור)
            time.sleep(0.4)
            נהג.execute_script("arguments[0].click();", כפתור)
            time.sleep(3)
            return True
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────────────────────────
# תיוק תיק יחיד
# ─────────────────────────────────────────────────────────────────────────────
def תייק_לקוח(נהג, לקוח, חלון):
    """
    מחזיר: (int נשמרו, 'המשך_תיוק') אם סאמיט קפץ לתיק הבא אוטומטית,
            או int רגיל אם חזרנו לדף הראשי.
    """
    שם = לקוח["שם"]
    חלון.כתוב(f"\n=== {שם} ===")
    חלון.כתוב(f"  כתובת: {לקוח['url']}")
    נהג.get(לקוח["url"])
    time.sleep(8)

    # קרא כמות אמיתית מהדף
    כמות = קרא_כמות_ממתינים(נהג) or לקוח.get("כמות", 1)
    חלון.כתוב(f"  {כמות} קבצים ממתינים")

    תויק = 0
    דולג = 0
    מצב_כפילות = [False]
    מסמך_נוכחי = 0
    דילוגים_רצופים = 0
    סף_בטיחות = max(כמות * 3, 500)

    while מסמך_נוכחי < סף_בטיחות:
        # השהה
        while חלון.מושהה and not חלון.דלג_תיק:
            חלון.root.update()
            time.sleep(0.2)
        if חלון.דלג_תיק:
            חלון.כתוב("  ⏭ דילוג ידני לתיק הבא")
            חלון.דלג_תיק = False
            return תויק

        מסמך_נוכחי += 1
        חלון.כתוב(f"  מסמך {מסמך_נוכחי}…")

        # בדוק חלון לפני כל מסמך
        חלון_מיוחד = בדוק_חלונות_מיוחדים(נהג, חלון, מצב_כפילות, סוף_תיק=False)
        if חלון_מיוחד == 'סוף_קבצים':
            חלון.כתוב("  סוף הקבצים (לפני שמירה) — חוזר לקליטת חומרים")
            return תויק
        if חלון_מיוחד in ('כפילות', 'מחיקה'):
            continue

        ניתן, חסרים = האם_ניתן_לשמור(נהג)

        if ניתן:
            דילוגים_רצופים = 0  # איפוס — הצלחנו לשמור
            if לחץ_שמירה(נהג):
                חלון.כתוב("  ✓ נשמר")
                תויק += 1
            else:
                חלון.כתוב("  לא נמצא כפתור שמירה — מדלג")
                לחץ_דילוג(נהג)
                דולג += 1
                continue

            # בדוק אחרי שמירה
            חלון_אחרי = בדוק_חלונות_מיוחדים(נהג, חלון, מצב_כפילות, סוף_תיק=True)
            if isinstance(חלון_אחרי, tuple) and חלון_אחרי[0] == 'סוף_קבצים':
                # סאמיט עבר לתיק הבא אוטומטית
                שם_הבא = חלון_אחרי[1]
                חלון.כתוב(f"  סוף הקבצים — סאמיט עבר לתיק הבא{f': {שם_הבא}' if שם_הבא else ''}")
                return (תויק, 'המשך_תיוק', שם_הבא)
            if חלון_אחרי in ('כפילות', 'מחיקה'):
                continue

        else:
            חלון.כתוב(f"  חסרים: {', '.join(חסרים)} — מדלג")
            if כמות == 1:
                חלון.כתוב("  מסמך יחיד ללא שמירה — מסיים תיק")
                return תויק

            נשמר_דילוג = לחץ_דילוג(נהג)
            דולג += 1
            דילוגים_רצופים += 1

            # אם דילגנו יותר מפי 2 מכמות הקבצים המקורית — סיימנו את הקבצים
            if דילוגים_רצופים >= max(כמות * 2, 4):
                חלון.כתוב(f"  {דילוגים_רצופים} דילוגים רצופים — מסיים תיק")
                return תויק

            if not נשמר_דילוג:
                חלון.כתוב("  לא ניתן לדלג — עוצר")
                break
            time.sleep(1)

            חלון_אחרי = בדוק_חלונות_מיוחדים(נהג, חלון, מצב_כפילות, סוף_תיק=True)
            if isinstance(חלון_אחרי, tuple) and חלון_אחרי[0] == 'סוף_קבצים':
                שם_הבא = חלון_אחרי[1]
                חלון.כתוב(f"  סוף הקבצים אחרי דילוג — סאמיט עבר לתיק הבא{f': {שם_הבא}' if שם_הבא else ''}")
                return (תויק, 'המשך_תיוק', שם_הבא)

        # בדוק אם עזבנו את דף התיוק
        try:
            if "fileexpenses" not in נהג.current_url:
                חלון.כתוב("  יצאנו מדף התיוק")
                return תויק
        except Exception:
            return תויק

    חלון.כתוב(f"  הגענו לסף בטיחות ({סף_בטיחות})")
    return תויק


# ─────────────────────────────────────────────────────────────────────────────
# לולאת תיוק ראשית
# ─────────────────────────────────────────────────────────────────────────────
def הרץ_תיוק(חלון):
    print("DEBUG: הרץ_תיוק התחיל", flush=True)
    time.sleep(0.5)  # מחכה שה-GUI יתחיל
    print("DEBUG: כותב לחלון...", flush=True)
    חלון.כתוב("מחבר לכרום…")
    print("DEBUG: לפני חבר_לכרום", flush=True)
    try:
        נהג = חבר_לכרום()
        print("DEBUG: חובר לכרום בהצלחה", flush=True)
        חלון.כתוב("מחובר לכרום ✓")
    except Exception as שגיאה:
        print(f"DEBUG: שגיאה בכרום: {שגיאה}", flush=True)
        חלון.כתוב(f"שגיאה בחיבור לכרום: {שגיאה}")
        חלון.כתוב("ודא שהרצת את פתח_כרום_לסאמיט.bat")
        חלון.סיום()
        return

    # נווט לעמוד קליטת חומרים
    חלון.כתוב("מנווט לעמוד ראשי…")
    עבור_לקליטת_חומרים(נהג, חלון)
    חלון.כתוב(f"URL אחרי ניווט: {נהג.current_url}")

    # אסוף תיקים
    חלון.כתוב("סורק תיקים…")
    תיקים = מצא_תיקים_בקליטה(נהג, חלון)
    חלון.כתוב(f"סיום סריקה — נמצאו {len(תיקים)} תיקים")

    if not תיקים:
        חלון.כתוב("לא נמצאו תיקים לתיוק בקליטת חומרים.")
        חלון.סיום()
        return

    סה_כ_מסמכים = sum(ת["כמות"] for ת in תיקים)
    חלון.כתוב(f"סה\"כ: {len(תיקים)} תיקים, כ-{סה_כ_מסמכים} קבצים")
    חלון.כתוב("─" * 40)

    # תיוק לפי אינדקס
    אינדקס = 0
    סה_כ_נשמרו = 0
    סה_כ_בוצעו = 0
    סבב = 1

    while אינדקס < len(תיקים):
        if חלון.הפעל_מחדש:
            return

        לקוח = תיקים[אינדקס]
        תוצאה = תייק_לקוח(נהג, לקוח, חלון)

        if isinstance(תוצאה, tuple):
            נשמרו = תוצאה[0]
            שם_מחלון = תוצאה[2] if len(תוצאה) > 2 else None
            סה_כ_נשמרו += נשמרו
            סה_כ_בוצעו += לקוח["כמות"]

            # נסה קודם את השם שחולץ מחלון "סוף הקבצים"
            שם_חדש = שם_מחלון

            # גיבוי — קרא מכותרת הדפדפן
            if not שם_חדש:
                try:
                    שם_חדש = נהג.execute_script("""
                        var title = document.title || '';
                        var parts = title.split('|');
                        if (parts.length > 1) return parts[parts.length - 1].trim();
                        var bc = document.querySelector('nav .breadcrumb a:last-of-type, .breadcrumb-item:last-child a');
                        return bc ? bc.textContent.trim() : null;
                    """)
                except Exception:
                    pass

            if שם_חדש:
                # חפש בתוך הרשימה — התאמה חלקית גם
                נמצא = False
                for j, ת in enumerate(תיקים):
                    if ת["שם"] == שם_חדש or שם_חדש in ת["שם"] or ת["שם"] in שם_חדש:
                        אינדקס = j
                        חלון.כתוב(f"  [אוטומטי] עכשיו בתיק: {ת['שם']}")
                        נמצא = True
                        break
                if not נמצא:
                    חלון.כתוב(f"  [אוטומטי] לא זוהה '{שם_חדש}' ברשימה — ממשיך לבא")
                    אינדקס += 1
            else:
                אינדקס += 1
        else:
            נשמרו = תוצאה
            סה_כ_נשמרו += נשמרו
            סה_כ_בוצעו += לקוח["כמות"]
            אינדקס += 1
            pass  # URL ישיר — אין צורך לחזור לקליטת חומרים

        if חלון.הפעל_מחדש:
            return

    # בדיקה חוזרת — אולי נוצרו תיקים חדשים בזמן שעבדנו
    חלון.כתוב("\n" + "─" * 40)
    חלון.כתוב("בדיקה חוזרת — מחפש תיקים נוספים…")
    עבור_לקליטת_חומרים(נהג, חלון)
    תיקים_נוספים = מצא_תיקים_בקליטה(נהג, חלון)

    if תיקים_נוספים:
        חלון.כתוב(f"נמצאו {len(תיקים_נוספים)} תיקים נוספים — ממשיך")
        חלון.כתוב("─" * 40)
        תיקים = תיקים_נוספים
        אינדקס = 0
        סבב += 1
        # לולאה חוזרת
        while אינדקס < len(תיקים):
            if חלון.הפעל_מחדש:
                return
            לקוח = תיקים[אינדקס]
            תוצאה = תייק_לקוח(נהג, לקוח, חלון)
            if isinstance(תוצאה, tuple):
                נשמרו = תוצאה[0]
                סה_כ_נשמרו += נשמרו
                סה_כ_בוצעו += לקוח["כמות"]
            else:
                נשמרו = תוצאה
                סה_כ_נשמרו += נשמרו
                סה_כ_בוצעו += לקוח["כמות"]
            אינדקס += 1
            if חלון.הפעל_מחדש:
                return
    else:
        חלון.כתוב("אין תיקים נוספים ✓")

    # סיכום
    חלון.כתוב("\n" + "═" * 40)
    חלון.כתוב(f"סיום התיוק!")
    חלון.כתוב(f"תיקים שטופלו : {len(תיקים)}")
    חלון.כתוב(f"קבצים שנסרקו: {סה_כ_בוצעו}")
    חלון.כתוב(f"מסמכים שנשמרו: {סה_כ_נשמרו}")
    חלון.כתוב("═" * 40)
    חלון.סיום()


# ─────────────────────────────────────────────────────────────────────────────
# חלון התקדמות
# ─────────────────────────────────────────────────────────────────────────────
class חלון_התקדמות:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("תיוק בתהליך — סאמיט")
        self.root.geometry("560x500")
        self.root.configure(bg="#f0f4f8")
        self.מושהה = False
        self.דלג_תיק = False
        self.הפעל_מחדש = False
        self._תור = queue.Queue()
        self._בנה()
        self._עדכן_תור()

    def _בנה(self):
        # כותרת
        tk.Label(self.root, text="תיוק אוטומטי — סאמיט",
                 font=("Arial", 13, "bold"), bg="#1a4fa0", fg="white",
                 pady=10).pack(fill="x")

        # יומן
        מסגרת = tk.Frame(self.root, bg="#f0f4f8")
        מסגרת.pack(padx=14, pady=(12, 6), fill="both", expand=True)
        גלילה = tk.Scrollbar(מסגרת)
        גלילה.pack(side="right", fill="y")
        self.יומן = tk.Text(מסגרת, font=("Courier New", 10), bg="#1a2a4a", fg="#a0e0a0",
                             wrap="word", yscrollcommand=גלילה.set,
                             state="disabled", width=64, height=22)
        self.יומן.pack(side="left", fill="both", expand=True)
        גלילה.config(command=self.יומן.yview)

        # כפתורים
        מסגרת_כפתורים = tk.Frame(self.root, bg="#f0f4f8")
        מסגרת_כפתורים.pack(pady=8)

        def העתק():
            self.root.clipboard_clear()
            self.root.clipboard_append(self.יומן.get("1.0", "end"))

        tk.Button(מסגרת_כפתורים, text="📋 העתק הכל",
                  font=("Arial", 10), bg="#4b6fa8", fg="white",
                  padx=12, pady=5, relief="flat", cursor="hand2",
                  command=העתק).pack(side="left", padx=5)

        self.כפתור_השהה = tk.Button(
            מסגרת_כפתורים, text="⏸ השהה",
            font=("Arial", 10), bg="#e67e22", fg="white",
            padx=12, pady=5, relief="flat", cursor="hand2",
            command=self._שנה_השהיה)
        self.כפתור_השהה.pack(side="left", padx=5)

        tk.Button(מסגרת_כפתורים, text="⏭ תיק הבא",
                  font=("Arial", 10), bg="#8e44ad", fg="white",
                  padx=12, pady=5, relief="flat", cursor="hand2",
                  command=self._דלג_תיק).pack(side="left", padx=5)

        tk.Button(מסגרת_כפתורים, text="🔄 הפעל מחדש",
                  font=("Arial", 10), bg="#27ae60", fg="white",
                  padx=12, pady=5, relief="flat", cursor="hand2",
                  command=self._הפעל_מחדש).pack(side="left", padx=5)

        self.כפתור_סגור = tk.Button(
            מסגרת_כפתורים, text="✖ סגור",
            font=("Arial", 10), bg="#888", fg="white",
            padx=12, pady=5, relief="flat", cursor="hand2",
            command=self.root.destroy, state="disabled")
        self.כפתור_סגור.pack(side="left", padx=5)

    def _שנה_השהיה(self):
        self.מושהה = not self.מושהה
        if self.מושהה:
            self.כפתור_השהה.config(text="▶ המשך", bg="#27ae60")
            self.כתוב("  ⏸ מושהה — ניתן לערוך בסאמיט")
        else:
            self.כפתור_השהה.config(text="⏸ השהה", bg="#e67e22")
            self.כתוב("  ▶ ממשיך…")

    def _דלג_תיק(self):
        self.דלג_תיק = True
        if self.מושהה:
            self._שנה_השהיה()

    def _הפעל_מחדש(self):
        self.הפעל_מחדש = True
        if self.מושהה:
            self.מושהה = False
        self.כתוב("\n🔄 מפעיל מחדש…")
        try:
            self.root.quit()
        except Exception:
            pass

    def _עדכן_תור(self):
        """רץ ב-mainloop — מוציא הודעות מהתור ומציג אותן."""
        try:
            while True:
                טקסט = self._תור.get_nowait()
                if טקסט == "__סיום__":
                    self._הפעל_סיום()
                    continue
                self.יומן.config(state="normal")
                self.יומן.insert("end", טקסט + "\n")
                self.יומן.see("end")
                self.יומן.config(state="disabled")
        except queue.Empty:
            pass
        except Exception:
            pass
        self.root.after(50, self._עדכן_תור)

    def כתוב(self, טקסט):
        """בטוח לקריאה מכל ת'רד."""
        try:
            self._תור.put(טקסט)
        except Exception:
            pass

    def סיום(self):
        try:
            self._תור.put("__סיום__")
        except Exception:
            pass

    def _הפעל_סיום(self):
        try:
            self.כפתור_סגור.config(state="normal")
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# חלון פתיחה
# ─────────────────────────────────────────────────────────────────────────────
def חלון_פתיחה():
    root = tk.Tk()
    root.title("כלי תיוק סאמיט")
    root.geometry("420x230")
    root.resizable(False, False)
    root.configure(bg="#f0f4f8")
    תוצאה = {"התחל": False}

    tk.Label(root, text="כלי תיוק אוטומטי לסאמיט",
             font=("Arial", 15, "bold"), bg="#f0f4f8").pack(pady=(28, 6))
    tk.Label(root,
             text="נכנס לעמוד 'קליטת חומרים'\n"
                  "ומתייק אוטומטית כשכל שדות החובה מולאו",
             font=("Arial", 10), bg="#f0f4f8", fg="#555",
             justify="center").pack(pady=(0, 6))
    tk.Label(root, text="ודא שכרום פתוח עם סאמיט לפני שמתחילים",
             font=("Arial", 9), bg="#f0f4f8", fg="#c05000").pack(pady=(0, 18))

    מסגרת = tk.Frame(root, bg="#f0f4f8")
    מסגרת.pack()

    def התחל():
        תוצאה["התחל"] = True
        root.destroy()

    tk.Button(מסגרת, text="התחל תיוק", font=("Arial", 11, "bold"),
              bg="#1a56db", fg="white", padx=18, pady=7, relief="flat",
              cursor="hand2", command=התחל).pack(side="left", padx=8)
    tk.Button(מסגרת, text="ביטול", font=("Arial", 11),
              bg="#ccc", fg="#333", padx=18, pady=7, relief="flat",
              cursor="hand2", command=root.destroy).pack(side="left", padx=8)
    root.mainloop()
    return תוצאה


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    הגדרות = חלון_פתיחה()
    if not הגדרות["התחל"]:
        return

    חלון = חלון_התקדמות()
    while True:
        חלון.הפעל_מחדש = False
        threading.Thread(target=הרץ_תיוק, args=(חלון,), daemon=True).start()
        try:
            חלון.root.mainloop()
        except Exception:
            pass
        if not חלון.הפעל_מחדש:
            break
        # הפעל מחדש — צור חלון חדש
        חלון = חלון_התקדמות()


if __name__ == "__main__":
    main()
