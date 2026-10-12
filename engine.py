import json, os, time, urllib.request, urllib.error
from datetime import datetime, timezone

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT = str(os.environ["TELEGRAM_CHAT_ID"])
KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

PROMPT = """أنت محلل أحداث لموقع عن مجتمع Mystery Town (لعبة رول بلاي). حلل النص التالي واكتب بطاقة.
القواعد: لا تضف أي اسم أو واقعة غير موجودة في النص. ما كان تفسيراً منك اكتبه كقراءة وليس كحقيقة. إن كان النص غير كافٍ أعد {"skip": true}.
أعد JSON فقط بهذا الشكل: {"type": "حدث" أو "واقعة", "tags": ["لغز"] إن كان لغزاً وإلا [], "certainty": "مؤكد" أو "غير مؤكد" أو "إشاعة", "title": "عنوان قصير", "body": "من جملتين إلى 4 جمل"}
النص:
"""

def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)

def post_json(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"HTTP {e.code}: {detail}")

def tg(method, **params):
    res = post_json(f"https://api.telegram.org/bot{TOKEN}/{method}", params)
    return res.get("result")

LAST_ERR = ""

def analyze(text):
    global LAST_ERR
    LAST_ERR = ""
    body = {"contents": [{"parts": [{"text": PROMPT + text[:4000]}]}],
            "generationConfig": {"responseMimeType": "application/json"}}
    for model in [MODEL, "gemini-flash-latest"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={KEY}"
        try:
            res = post_json(url, body)
            raw = res["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(raw)
        except Exception as e:
            LAST_ERR = f"[{model}] {e}"
            print("gemini error:", LAST_ERR)
    return None

def clean(d):
    if not isinstance(d, dict) or d.get("skip"):
        return None
    t = d.get("type") if d.get("type") in ("حدث", "واقعة") else "حدث"
    c = d.get("certainty") if d.get("certainty") in ("مؤكد", "غير مؤكد", "إشاعة") else "غير مؤكد"
    tags = [x for x in (d.get("tags") or []) if x == "لغز"]
    title = str(d.get("title", "")).strip()[:120]
    body = str(d.get("body", "")).strip()[:900]
    if not title or not body:
        return None
    return {"type": t, "tags": tags, "certainty": c, "title": title, "body": body}

def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def on_message(m, uid, state):
    if str(m["chat"]["id"]) != CHAT:
        return
    text = m.get("text") or m.get("caption")
    if not text or text.startswith("/start"):
        tg("sendMessage", chat_id=CHAT, text="أرسل لي نص المنشور أو شاركه هنا، وسأجهز لك مسودة تحليل.")
        return
    card = clean(analyze(text))
    if not card:
        msg = "لم أستطع كتابة تحليل من هذا النص. أضف تفاصيل أو أعد الإرسال لاحقاً."
        if LAST_ERR:
            msg = "خطأ من Gemini:\n" + LAST_ERR[:700]
        tg("sendMessage", chat_id=CHAT, text=msg)
        return
    when = (m.get("forward_origin") or {}).get("date") or m["date"]
    card["id"] = str(uid)
    card["time"] = iso(when)
    state["drafts"][card["id"]] = card
    preview = f"مسودة\n{card['type']} · {card['certainty']}\n\n{card['title']}\n\n{card['body']}"
    kb = {"inline_keyboard": [[{"text": "نشر", "callback_data": "pub:" + card["id"]},
                               {"text": "حذف", "callback_data": "del:" + card["id"]}]]}
    tg("sendMessage", chat_id=CHAT, text=preview, reply_markup=kb)

def on_callback(cq, state, posts):
    if str(cq["message"]["chat"]["id"]) != CHAT:
        return
    action, _, did = cq["data"].partition(":")
    card = state["drafts"].pop(did, None)
    if card and action == "pub":
        posts.insert(0, card)
        note = "تم النشر"
    elif card:
        note = "تم الحذف"
    else:
        note = "المسودة غير موجودة"
    tg("answerCallbackQuery", callback_query_id=cq["id"], text=note)
    tg("editMessageReplyMarkup", chat_id=CHAT, message_id=cq["message"]["message_id"],
       reply_markup={"inline_keyboard": []})

def main():
    state = load("state.json", {"offset": 0, "drafts": {}})
    posts = load("posts.json", [])
    updates = tg("getUpdates", offset=state["offset"], timeout=0,
                 allowed_updates=["message", "callback_query"]) or []
    for u in updates:
        state["offset"] = u["update_id"] + 1
        try:
            if "callback_query" in u:
                on_callback(u["callback_query"], state, posts)
            elif "message" in u:
                on_message(u["message"], u["update_id"], state)
        except Exception as e:
            print("update error:", e)
    save("state.json", state)
    save("posts.json", posts)

if __name__ == "__main__":
    main()
