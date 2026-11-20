# -*- coding: utf-8 -*-
"""
⛽ الروبوت التلقائي لأسعار البترول (كل دول الخليج)
- يدوّر على خبر الأسعار الجديدة في الأخبار (Bing News)
- يقرا الأرقام ويتأكد إنها منطقية (قريبة من الشهر اللي قبل ±٢٥٪ ومرتبة)
- إذا سليمة: يحدّث ملف الدولة + يرسل إشعار لأهلها
- إذا شك: يفتح Issue (تنبيه) لسعيد يشيّك بعينه
"""
import datetime, html, json, os, re, subprocess, sys, urllib.parse

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; fuel-prices-bot/1.0)"}
AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
KEYS = ["super98", "special95", "eplus91", "diesel"]

# لكل دولة: الملف، كلمات البحث، أسماء الأنواع (للقراءة من الخبر)، العملة، ومتى تعلن
COUNTRIES = {
    "AE": {"file": "prices.json", "name": "UAE", "words": ["uae", "emirates"],
           "labels": [r"super\s*98", r"special\s*95", r"e[\s\-]?plus(?:\s*91)?", r"diesel"],
           "milli": False, "days": "monthly_end", "tz": 4},
    "SA": {"file": "prices_sa.json", "name": "Saudi Aramco gasoline", "words": ["saudi", "aramco", "ksa"],
           "labels": [None, r"(?:gasoline|petrol|octane)?\s*95", r"(?:gasoline|petrol|octane)?\s*91", r"diesel"],
           "milli": False, "days": "monthly_mid", "tz": 3},
    "QA": {"file": "prices_qa.json", "name": "Qatar", "words": ["qatar", "qatarenergy"],
           "labels": [None, r"super(?:\s*(?:grade|gasoline|petrol))?(?:\s*95)?", r"premium(?:\s*(?:grade|gasoline|petrol))?(?:\s*91)?", r"diesel"],
           "milli": False, "days": "monthly_end", "tz": 3},
    "OM": {"file": "prices_om.json", "name": "Oman", "words": ["oman", "muscat"],
           "labels": [None, r"m\s*95|95\s*octane|super", r"m\s*91|91\s*octane|regular", r"diesel"],
           "milli": True, "days": "monthly_end", "tz": 4},
    "KW": {"file": "prices_kw.json", "name": "Kuwait", "words": ["kuwait", "knpc"],
           "labels": [r"ultra(?:\s*98)?", r"super(?:\s*95)?", r"premium(?:\s*91)?", r"diesel"],
           "milli": True, "days": "quarterly", "tz": 3},
    "BH": {"file": "prices_bh.json", "name": "Bahrain", "words": ["bahrain"],
           "labels": [r"super(?:\s*98)?", r"mumtaz(?:\s*95)?", r"jayyid(?:\s*91)?", r"diesel"],
           "milli": True, "days": "any", "tz": 3},
}


def fetch(url):
    try:
        r = requests.get(url, headers=UA, timeout=25)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print("fetch failed", url, e)
    return ""


def clean(text):
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text))


def target_month(cfg, now):
    """الشهر اللي ننتظر أسعاره الحين (أو None إذا مب وقت إعلان)."""
    d = now.day
    kind = cfg["days"]
    if kind == "monthly_end":
        if d >= 27:
            return (now.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
        if d <= 3:
            return now.replace(day=1)
        return None
    if kind == "monthly_mid":   # أرامكو: حوالي يوم ١٠-١١ من الشهر
        return now.replace(day=1) if 9 <= d <= 14 else None
    if kind == "quarterly":     # الكويت: آخر الربع (مارس، يونيو، سبتمبر، ديسمبر)
        if now.month in (3, 6, 9, 12) and d >= 25:
            return (now.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
        if now.month in (1, 4, 7, 10) and d <= 3:
            return now.replace(day=1)
        return None
    return now.replace(day=1) if d <= 5 or d >= 27 else None   # البحرين: نشيّك أول وآخر الشهر


def read_number(text, label, milli):
    """يلقى أول رقم بعد اسم النوع. الدينار/الريال العماني: الفلس/البيسة ← /١٠٠٠."""
    m = re.search(r"(?:" + label + r")[^0-9]{0,80}?(\d{1,3}(?:\.\d{1,3})?)\s*(fils|baisa|baiza|bz|ريال|riyal|qr|sar|dhs|aed|kd|bd|omr)?",
                  text, flags=re.I)
    if not m:
        return None
    v = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if milli and (unit in ("fils", "baisa", "baiza", "bz") or v >= 20):
        v = v / 1000.0
    return round(v, 3)


def extract(cfg, text, current):
    out = []
    for i, label in enumerate(cfg["labels"]):
        if label is None:
            out.append(None)
            continue
        v = read_number(text, label, cfg["milli"])
        cur = current[i]
        # لازم يكون قريب من السعر الحالي (±٢٥٪)، وإلا نشك
        if v is None or cur is None or not (cur * 0.75 <= v <= cur * 1.25):
            return None
        out.append(v)
    # ترتيب منطقي: الأعلى أوكتان أغلى (أو يساوي)
    g = [x for x in out[:3] if x is not None]
    if any(g[k] < g[k + 1] for k in range(len(g) - 1)):
        return None
    return out


def news(cfg, en_name, year):
    query = f"{cfg['name']} fuel prices {en_name} {year}"
    # مصدرين: أخبار Bing + أخبار Google (تلقط خبر وام بسرعة)
    rss = fetch("https://www.bing.com/news/search?format=rss&q=" + urllib.parse.quote(query))
    rss += fetch("https://news.google.com/rss/search?hl=en-AE&gl=AE&ceid=AE:en&q=" + urllib.parse.quote(query + " when:3d"))
    found = []
    for it in re.findall(r"<item>(.*?)</item>", rss, flags=re.S):
        title = html.unescape(re.sub(r"<.*?>", "", (re.search(r"<title>(.*?)</title>", it, re.S) or [None, ""])[1]))
        link = html.unescape((re.search(r"<link>(.*?)</link>", it, re.S) or [None, ""])[1]).strip()
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        if "url" in qs:
            link = qs["url"][0]
        tl = title.lower()
        if (("fuel" in tl or "petrol" in tl or "gasoline" in tl or "diesel" in tl)
                and en_name.lower() in tl and any(w in tl for w in cfg["words"])):
            found.append((title, link))
    return found


def send_push(topic_prefix, title_ar, body_ar, title_en, body_en):
    key = os.environ.get("FIREBASE_KEY")
    if not key:
        print("no firebase key")
        return
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    info = json.loads(key)
    creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    creds.refresh(Request())
    url = f"https://fcm.googleapis.com/v1/projects/{info['project_id']}/messages:send"
    for lang, t, b in (("ar", title_ar, body_ar), ("en", title_en, body_en)):
        r = requests.post(url, timeout=30, headers={"Authorization": "Bearer " + creds.token},
                          json={"message": {"topic": f"{topic_prefix}{lang}", "notification": {"title": t, "body": b},
                                            "android": {"priority": "HIGH"}}})
        print("push", topic_prefix + lang, r.status_code)


def gh(*args):
    return subprocess.run(["gh", *args, "--repo", os.environ.get("REPO", "")], capture_output=True, text=True)


def main():
    changed = []
    for code, cfg in COUNTRIES.items():
        now = datetime.datetime.utcnow() + datetime.timedelta(hours=cfg["tz"])
        t = target_month(cfg, now)
        if t is None:
            continue
        ar_name, en_name = AR_MONTHS[t.month - 1], EN_MONTHS[t.month - 1]
        label = f"{ar_name} {t.year}"
        try:
            data = json.load(open(cfg["file"], encoding="utf-8"))
        except Exception:
            print(code, "no file")
            continue
        if data.get("month") == label:
            print(code, "already", label)
            continue
        current = [data.get(k) for k in KEYS]
        found = news(cfg, en_name, t.year)
        print(code, "news:", found[:3])
        prices, source = None, None
        for title, link in found[:4]:
            p = extract(cfg, clean(title + " " + fetch(link)), current)
            if p:
                prices, source = p, link
                break
        if not prices:
            if found and code != "AE":
                # لقينا خبر بس ما قدرنا نقرا الأرقام بثقة: تنبيه لسعيد
                title = f"⛽ {code} أسعار {label}: شيّك بعينك"
                lst = gh("issue", "list", "--state", "all", "--search", title, "--json", "title")
                if title not in (lst.stdout or ""):
                    body = "الروبوت لقى الخبر بس ما تأكد من الأرقام:\n" + "\n".join(f"- [{a}]({b})" for a, b in found[:3])
                    body += f"\n\nعدّل الملف `{cfg['file']}` بيدك."
                    gh("issue", "create", "--title", title, "--body", body)
            continue

        # ✅ نحدّث الملف
        out = {"month": label, "source": source}
        for k, v, old in zip(KEYS, prices, current):
            if v is not None:
                out[k] = v
                if old is not None:
                    out["prev_" + k] = old
        if "poll_closed" in data:
            out["poll_closed"] = data["poll_closed"]
        with open(cfg["file"], "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

        # الإمارات: نحدّث تاريخ الأسعار بعد
        if code == "AE":
            key = f"{t.year}-{t.month:02d}"
            try:
                hist = json.load(open("history.json", encoding="utf-8"))
                months = [m for m in hist.get("months", []) if m.get("m") != key]
                months.append({"m": key, "p": prices})
                months.sort(key=lambda m: m["m"])
                hist["months"] = months
                with open("history.json", "w", encoding="utf-8") as f:
                    json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))
            except Exception as e:
                print("history failed", e)
        changed.append((code, cfg, label, prices, current))

    if not changed:
        print("nothing to update")
        return
    subprocess.run(["git", "config", "user.name", "prices-bot"])
    subprocess.run(["git", "config", "user.email", "prices-bot@users.noreply.github.com"])
    subprocess.run(["git", "add", "-A", "prices.json", "history.json"] + [c["file"] for c in COUNTRIES.values()])
    subprocess.run(["git", "commit", "-m", "Auto update fuel prices: " + ", ".join(c[0] for c in changed)])
    subprocess.run(["git", "pull", "--rebase"])
    subprocess.run(["git", "push"])

    for code, cfg, label, prices, current in changed:
        if code == "AE":
            gh("workflow", "run", "notify.yml")   # إشعار الإمارات المعتاد
            continue
        up = any(v is not None and o is not None and v > o for v, o in zip(prices, current))
        down = any(v is not None and o is not None and v < o for v, o in zip(prices, current))
        word_ar = "ارتفعت 📈" if up and not down else ("انخفضت 📉" if down and not up else ("بدون تغيير" if not up and not down else "تغيّرت"))
        word_en = "up 📈" if up and not down else ("down 📉" if down and not up else ("unchanged" if not up and not down else "changed"))
        send_push(f"prices_{code.lower()}_", f"⛽ أسعار {label} {word_ar}", "افتح التطبيق وشوف كم تكلّفك التعبئة",
                  f"⛽ {EN_MONTHS[AR_MONTHS.index(label.split()[0])]} fuel prices are {word_en}", "Open the app to see your full-tank cost")


if __name__ == "__main__":
    main()
