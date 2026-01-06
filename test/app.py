import os
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, request, render_template

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

DB_PATH = os.path.join(os.path.dirname(__file__), "othello.db")

DIRECTIONS = [
    (-1, -1), (0, -1), (1, -1),
    (-1,  0),          (1,  0),
    (-1,  1), (0,  1), (1,  1),
]

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        board TEXT NOT NULL,                 -- 64 chars (row-major), '.' 'B' 'W'
        current_player TEXT NOT NULL,        -- 'B' or 'W'
        status TEXT NOT NULL,                -- 'active' or 'finished'
        winner TEXT,                         -- 'B' 'W' 'D' (draw) or NULL
        black_count INTEGER NOT NULL,
        white_count INTEGER NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS moves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id INTEGER NOT NULL,
        move_no INTEGER NOT NULL,
        player TEXT NOT NULL,
        x INTEGER,
        y INTEGER,
        flipped INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(game_id) REFERENCES games(id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id INTEGER NOT NULL UNIQUE,
        played_at TEXT NOT NULL,
        winner TEXT NOT NULL,
        black_count INTEGER NOT NULL,
        white_count INTEGER NOT NULL,
        total_moves INTEGER NOT NULL,
        FOREIGN KEY(game_id) REFERENCES games(id)
    );
    """)

    conn.commit()
    conn.close()

def opponent(p: str) -> str:
    return "W" if p == "B" else "B"

def deserialize_board(s: str):
    s = s.strip()
    return [list(s[i*8:(i+1)*8]) for i in range(8)]

def serialize_board(board):
    return "".join("".join(row) for row in board)

def count_discs(board):
    b = sum(cell == "B" for row in board for cell in row)
    w = sum(cell == "W" for row in board for cell in row)
    return b, w

def in_bounds(x, y):
    return 0 <= x < 8 and 0 <= y < 8

def flips_for_move(board, x, y, player):
    if not in_bounds(x, y) or board[y][x] != ".":
        return []
    opp = opponent(player)
    flips = []

    for dx, dy in DIRECTIONS:
        cx, cy = x + dx, y + dy
        line = []
        while in_bounds(cx, cy) and board[cy][cx] == opp:
            line.append((cx, cy))
            cx += dx
            cy += dy
        # line ends: must be player disc to bracket
        if line and in_bounds(cx, cy) and board[cy][cx] == player:
            flips.extend(line)

    return flips

def legal_moves(board, player):
    moves = {}
    for y in range(8):
        for x in range(8):
            fl = flips_for_move(board, x, y, player)
            if fl:
                moves[(x, y)] = fl
    return moves

def initial_board():
    board = [["." for _ in range(8)] for _ in range(8)]
    # standard start
    board[3][3] = "W"
    board[4][4] = "W"
    board[3][4] = "B"
    board[4][3] = "B"
    return board

def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def ensure_turn_is_playable(board, current_player):
    """
    If current player has no moves, try pass.
    Returns (new_current_player, message)
    """
    cur_moves = legal_moves(board, current_player)
    if cur_moves:
        return current_player, None

    opp = opponent(current_player)
    opp_moves = legal_moves(board, opp)
    if opp_moves:
        return opp, f"{'黒' if current_player=='B' else '白'}は置ける場所がないためパス。次は{'黒' if opp=='B' else '白'}。"

    # nobody can move -> game ends
    return current_player, "両者とも置ける場所がないため終了。"

def finish_game_if_needed(conn, game_id, board):
    """
    If game ended, update games, insert matches.
    Returns (status, winner, message or None)
    """
    b, w = count_discs(board)

    # end condition: no legal moves for both
    if legal_moves(board, "B") or legal_moves(board, "W"):
        return "active", None, None

    if b > w:
        winner = "B"
    elif w > b:
        winner = "W"
    else:
        winner = "D"

    cur = conn.cursor()
    cur.execute("""
        UPDATE games
        SET status='finished', winner=?, updated_at=?, black_count=?, white_count=?
        WHERE id=?
    """, (winner, now_iso(), b, w, game_id))

    # total moves
    cur.execute("SELECT COUNT(*) AS c FROM moves WHERE game_id=?", (game_id,))
    total_moves = cur.fetchone()["c"]

    # insert match if not exists
    cur.execute("""
        INSERT OR IGNORE INTO matches(game_id, played_at, winner, black_count, white_count, total_moves)
        VALUES(?, ?, ?, ?, ?, ?)
    """, (game_id, now_iso(), winner, b, w, total_moves))

    conn.commit()

    msg = f"ゲーム終了：黒 {b} / 白 {w}。勝者：{'黒' if winner=='B' else '白' if winner=='W' else '引き分け'}"
    return "finished", winner, msg

def game_state_payload(conn, game_row, extra_message=None):
    board = deserialize_board(game_row["board"])
    current_player = game_row["current_player"]
    status = game_row["status"]
    winner = game_row["winner"]

    # If active, handle pass logic at "state fetch" time too
    message = extra_message
    if status == "active":
        new_cp, pass_msg = ensure_turn_is_playable(board, current_player)
        if new_cp != current_player:
            # update current player if pass
            cur = conn.cursor()
            cur.execute("""
                UPDATE games SET current_player=?, updated_at=? WHERE id=?
            """, (new_cp, now_iso(), game_row["id"]))
            conn.commit()
            current_player = new_cp
            message = pass_msg if pass_msg else message

        # check finish
        status2, winner2, fin_msg = finish_game_if_needed(conn, game_row["id"], board)
        if status2 == "finished":
            status, winner = status2, winner2
            message = fin_msg if fin_msg else message

    b, w = count_discs(board)
    moves = legal_moves(board, current_player) if status == "active" else {}
    legal_list = [{"x": x, "y": y} for (x, y) in moves.keys()]

    return {
        "game_id": game_row["id"],
        "board": board,  # 2D
        "current_player": current_player,
        "status": status,
        "winner": winner,  # 'B' 'W' 'D' or None
        "black_count": b,
        "white_count": w,
        "legal_moves": legal_list,
        "message": message,
    }

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/new", methods=["POST"])
def api_new():
    board = initial_board()
    b, w = count_discs(board)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO games(created_at, updated_at, board, current_player, status, winner, black_count, white_count)
        VALUES(?, ?, ?, 'B', 'active', NULL, ?, ?)
    """, (now_iso(), now_iso(), serialize_board(board), b, w))
    game_id = cur.lastrowid

    conn.commit()

    cur.execute("SELECT * FROM games WHERE id=?", (game_id,))
    row = cur.fetchone()
    payload = game_state_payload(conn, row, extra_message="新規ゲーム開始：黒の番です。")
    conn.close()
    return jsonify(payload)

@app.route("/api/state/<int:game_id>", methods=["GET"])
def api_state(game_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM games WHERE id=?", (game_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "game not found"}), 404

    payload = game_state_payload(conn, row)
    conn.close()
    return jsonify(payload)

@app.route("/api/move", methods=["POST"])
def api_move():
    data = request.get_json(force=True)
    game_id = int(data.get("game_id"))
    x = data.get("x")
    y = data.get("y")

    if x is None or y is None:
        return jsonify({"error": "x,y required"}), 400

    x = int(x)
    y = int(y)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM games WHERE id=?", (game_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "game not found"}), 404

    if row["status"] != "active":
        payload = game_state_payload(conn, row, extra_message="このゲームは既に終了しています。")
        conn.close()
        return jsonify(payload)

    board = deserialize_board(row["board"])
    player = row["current_player"]

    fl = flips_for_move(board, x, y, player)
    if not fl:
        payload = game_state_payload(conn, row, extra_message="そこには置けません（合法手ではありません）。")
        conn.close()
        return jsonify(payload), 400

    # apply move
    board[y][x] = player
    for fx, fy in fl:
        board[fy][fx] = player

    b, w = count_discs(board)

    # move_no
    cur.execute("SELECT COUNT(*) AS c FROM moves WHERE game_id=?", (game_id,))
    move_no = cur.fetchone()["c"] + 1

    cur.execute("""
        INSERT INTO moves(game_id, move_no, player, x, y, flipped, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
    """, (game_id, move_no, player, x, y, len(fl), now_iso()))

    # decide next player (pass rules included later in game_state_payload)
    next_player = opponent(player)

    cur.execute("""
        UPDATE games
        SET board=?, current_player=?, updated_at=?, black_count=?, white_count=?
        WHERE id=?
    """, (serialize_board(board), next_player, now_iso(), b, w, game_id))

    conn.commit()

    # reload row
    cur.execute("SELECT * FROM games WHERE id=?", (game_id,))
    row2 = cur.fetchone()

    payload = game_state_payload(conn, row2, extra_message=None)
    conn.close()
    return jsonify(payload)

@app.route("/api/history", methods=["GET"])
def api_history():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT m.game_id, m.played_at, m.winner, m.black_count, m.white_count, m.total_moves
        FROM matches m
        ORDER BY m.played_at DESC
        LIMIT 50
    """)
    rows = cur.fetchall()
    conn.close()

    def win_label(w):
        if w == "B":
            return "黒"
        if w == "W":
            return "白"
        return "引き分け"

    history = []
    for r in rows:
        history.append({
            "game_id": r["game_id"],
            "played_at": r["played_at"],
            "winner": win_label(r["winner"]),
            "black_count": r["black_count"],
            "white_count": r["white_count"],
            "total_moves": r["total_moves"],
        })
    return jsonify({"history": history})

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "3000"))
    app.run(host="0.0.0.0", port=port, debug=True)
