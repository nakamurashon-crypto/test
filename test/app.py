import os
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string

# -----------------------------
# App config
# -----------------------------
app = Flask(__name__)

# Cloud Run / local 共通
PORT = int(os.environ.get("PORT", "8080"))

# SQLite DB path
# - Cloud Run: /tmp is writable (but not durable)
# - Windows / local: use project directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

default_db = os.path.join(BASE_DIR, "chat.db")
if os.name != "nt":  # not Windows
    default_db = "/tmp/chat.db"

DB_PATH = os.environ.get("DB_PATH", default_db)
# -----------------------------
# DB helpers
# -----------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    # Ensure parent dir exists (for custom DB_PATH)
    parent = os.path.dirname(DB_PATH)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


def add_message(session_id: str, role: str, content: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, datetime.utcnow().isoformat())
        )
        conn.commit()

def fetch_messages(session_id: str, limit: int = 50):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, session_id, role, content, created_at FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit)
        ).fetchall()
    return list(reversed(rows))

def list_sessions(limit: int = 20):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT session_id, MAX(created_at) AS last_time, COUNT(*) AS msg_count
            FROM messages
            GROUP BY session_id
            ORDER BY last_time DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return rows

# -----------------------------
# Bot logic (rule-based)
# -----------------------------
def generate_reply(user_text: str, history_rows):
    """
    history_rows: sqlite rows of past messages (role/content)
    Replace this function later if you want to call an LLM.
    """
    t = (user_text or "").strip()
    t_low = t.lower()

    # Simple intents
    if not t:
        return "何か入力してくれ。空だと反応できない。"

    if "help" in t_low or "使い方" in t or "ヘルプ" in t:
        return (
            "このボットはデモ用のチャットです。\n"
            "・挨拶：こんにちは / hi\n"
            "・時間：時間 / time\n"
            "・要約：要約: <文章>\n"
            "・反射：それ以外は、内容を短く言い換えて返します。"
        )

    if "こんにちは" in t or "hi" in t_low or "hello" in t_low:
        return "こんにちは。今日は何を作る？ 目的（Why）から一緒に決めよう。"

    if "時間" in t or "time" in t_low:
        return f"UTC時刻は {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} です。"

    if t.startswith("要約:") or t.startswith("要約："):
        body = t.split(":", 1)[1].strip() if ":" in t else t.split("：", 1)[1].strip()
        if not body:
            return "要約したい文章を `要約: ...` の形で入れて。"
        # naive summary (first ~200 chars)
        short = body.replace("\n", " ").strip()
        if len(short) > 220:
            short = short[:220] + "…"
        return f"要約（雑）：{short}"

    # Use last user message as context hint
    last_user = None
    for r in reversed(history_rows):
        if r["role"] == "user":
            last_user = r["content"]
            break

    if last_user:
        return (
            "了解。いまの発言を噛み砕いて返す。\n"
            f"あなた：{t}\n\n"
            "質問：それは何のため？（Why1）\n"
            "→ 目的が分かると、実装の最短手が確定する。"
        )

    # default
    return (
        "受け取った。要点を確認する。\n"
        f"あなた：{t}\n\n"
        "まず1つだけ聞く。\n"
        "それ、何のため？（Why1）"
    )

# -----------------------------
# Web UI (single file template)
# -----------------------------
INDEX_HTML = """
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Flask Chatbot Demo</title>
  <style>
    body{font-family:system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background:#0b0f19; color:#e8eefc; margin:0;}
    .wrap{max-width:900px; margin:0 auto; padding:24px;}
    .card{background:#121a2a; border:1px solid #22304b; border-radius:12px; padding:16px;}
    .row{display:flex; gap:12px; flex-wrap:wrap;}
    input,button,textarea{font:inherit;}
    .top{display:flex; justify-content:space-between; align-items:center; gap:12px;}
    .tag{font-size:12px; opacity:.8;}
    .chat{height:420px; overflow:auto; padding:12px; background:#0f1626; border-radius:10px; border:1px solid #22304b;}
    .msg{margin:10px 0; display:flex;}
    .bubble{max-width:78%; padding:10px 12px; border-radius:12px; line-height:1.4; white-space:pre-wrap;}
    .user{justify-content:flex-end;}
    .user .bubble{background:#2b5cff; color:white;}
    .assistant .bubble{background:#1a2640;}
    .controls{display:flex; gap:8px; margin-top:12px;}
    .controls input{flex:1; padding:10px 12px; border-radius:10px; border:1px solid #22304b; background:#0f1626; color:#e8eefc;}
    .controls button{padding:10px 12px; border-radius:10px; border:1px solid #22304b; background:#1a2640; color:#e8eefc; cursor:pointer;}
    .controls button:hover{filter:brightness(1.1);}
    .side{min-width:260px; flex:1;}
    .main{flex:2; min-width:320px;}
    a{color:#9cc0ff;}
    .sessions{max-height:420px; overflow:auto; padding:10px; background:#0f1626; border:1px solid #22304b; border-radius:10px;}
    .sess{display:block; padding:10px; border-radius:10px; text-decoration:none; color:#e8eefc;}
    .sess:hover{background:#17233b;}
    .muted{opacity:.7; font-size:12px;}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <h2 style="margin:0;">Flask Chatbot Demo</h2>
      <div class="tag">session: <b id="sid"></b></div>
    </div>

    <div class="row" style="margin-top:16px;">
      <div class="side">
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <b>セッション履歴</b>
            <button onclick="newSession()" style="padding:6px 10px;">新規</button>
          </div>
          <div class="muted" style="margin-top:6px;">クリックで切替。DBに保存されています。</div>
          <div class="sessions" id="sessions" style="margin-top:10px;"></div>
        </div>
      </div>

      <div class="main">
        <div class="card">
          <div class="chat" id="chat"></div>
          <div class="controls">
            <input id="text" placeholder="メッセージを入力（例：こんにちは / 時間 / 要約: ...）" />
            <button onclick="sendMsg()">送信</button>
          </div>
          <div class="muted" style="margin-top:10px;">
            API: <code>/api/chat</code> / 履歴: <code>/api/history?session_id=...</code>
          </div>
        </div>
      </div>
    </div>
  </div>

<script>
  function genId(){
    return "s_" + Math.random().toString(36).slice(2) + "_" + Date.now().toString(36);
  }

  function getSid(){
    const url = new URL(location.href);
    let sid = url.searchParams.get("session_id");
    if(!sid){
      sid = localStorage.getItem("session_id") || genId();
      url.searchParams.set("session_id", sid);
      history.replaceState(null, "", url.toString());
    }
    localStorage.setItem("session_id", sid);
    document.getElementById("sid").textContent = sid;
    return sid;
  }

  async function loadSessions(){
    const res = await fetch("/api/sessions");
    const data = await res.json();
    const box = document.getElementById("sessions");
    box.innerHTML = "";
    const current = getSid();

    data.sessions.forEach(s => {
      const a = document.createElement("a");
      a.className = "sess";
      a.href = "?session_id=" + encodeURIComponent(s.session_id);
      a.innerHTML = "<div><b>" + s.session_id + "</b></div>" +
                    "<div class='muted'>last: " + s.last_time + " / msgs: " + s.msg_count + "</div>";
      if(s.session_id === current){
        a.style.background = "#17233b";
      }
      box.appendChild(a);
    });
  }

  function renderChat(items){
    const chat = document.getElementById("chat");
    chat.innerHTML = "";
    items.forEach(m => {
      const div = document.createElement("div");
      div.className = "msg " + (m.role === "user" ? "user" : "assistant");
      const b = document.createElement("div");
      b.className = "bubble";
      b.textContent = m.content;
      div.appendChild(b);
      chat.appendChild(div);
    });
    chat.scrollTop = chat.scrollHeight;
  }

  async function loadHistory(){
    const sid = getSid();
    const res = await fetch("/api/history?session_id=" + encodeURIComponent(sid));
    const data = await res.json();
    renderChat(data.messages);
  }

  async function sendMsg(){
    const sid = getSid();
    const inp = document.getElementById("text");
    const text = inp.value;
    if(!text.trim()) return;
    inp.value = "";

    const res = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({session_id: sid, message: text})
    });
    const data = await res.json();
    await loadHistory();
    await loadSessions();
  }

  function newSession(){
    const sid = genId();
    const url = new URL(location.href);
    url.searchParams.set("session_id", sid);
    location.href = url.toString();
  }

  // enter to send
  document.addEventListener("keydown", (e) => {
    if(e.key === "Enter" && (document.activeElement && document.activeElement.id === "text")){
      sendMsg();
    }
  });

  (async function init(){
    getSid();
    await loadSessions();
    await loadHistory();
  })();
</script>
</body>
</html>
"""

# -----------------------------
# Routes
# -----------------------------

@app.get("/")
def index():
    return render_template_string(INDEX_HTML)

@app.get("/healthz")
def healthz():
    return "ok", 200

@app.get("/api/sessions")
def api_sessions():
    init_db()
    sessions = list_sessions(limit=50)
    return jsonify({
        "sessions": [
            {
                "session_id": r["session_id"],
                "last_time": r["last_time"],
                "msg_count": r["msg_count"]
            } for r in sessions
        ]
    })

@app.get("/api/history")
def api_history():
    init_db()
    session_id = request.args.get("session_id", "").strip()
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    rows = fetch_messages(session_id, limit=200)
    return jsonify({
        "session_id": session_id,
        "messages": [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "created_at": r["created_at"]
            } for r in rows
        ]
    })

@app.post("/api/chat")
def api_chat():
    init_db()
    data = request.get_json(silent=True) or {}
    session_id = (data.get("session_id") or "").strip()
    message = (data.get("message") or "").strip()

    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    if not message:
        return jsonify({"error": "message is required"}), 400

    # Store user message
    add_message(session_id, "user", message)

    # Generate reply using history
    history = fetch_messages(session_id, limit=50)
    reply = generate_reply(message, history)

    # Store assistant message
    add_message(session_id, "assistant", reply)

    return jsonify({"ok": True, "reply": reply})

# -----------------------------
# Entry
# -----------------------------
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=PORT, debug=True)
