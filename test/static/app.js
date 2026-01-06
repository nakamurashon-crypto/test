let gameId = null;
let legalSet = new Set();

const boardEl = document.getElementById("board");
const statusText = document.getElementById("statusText");
const messageEl = document.getElementById("message");
const blackCountEl = document.getElementById("blackCount");
const whiteCountEl = document.getElementById("whiteCount");
const turnTextEl = document.getElementById("turnText");
const historyListEl = document.getElementById("historyList");

function jpPlayer(p) {
  if (p === "B") return "黒";
  if (p === "W") return "白";
  return "-";
}

function keyXY(x,y){ return `${x},${y}`; }

function setMessage(msg) {
  messageEl.textContent = msg || "";
}

function renderBoard(board, legalMoves) {
  boardEl.innerHTML = "";
  legalSet = new Set(legalMoves.map(m => keyXY(m.x, m.y)));

  for (let y = 0; y < 8; y++) {
    for (let x = 0; x < 8; x++) {
      const cell = document.createElement("div");
      cell.className = "cell";
      cell.dataset.x = x;
      cell.dataset.y = y;

      const v = board[y][x];
      if (v === "B" || v === "W") {
        const disc = document.createElement("div");
        disc.className = `disc ${v === "B" ? "black" : "white"}`;
        cell.appendChild(disc);
      } else {
        if (legalSet.has(keyXY(x,y))) {
          const dot = document.createElement("div");
          dot.className = "legalDot";
          cell.appendChild(dot);
        }
      }

      cell.addEventListener("click", () => onCellClick(x, y));
      boardEl.appendChild(cell);
    }
  }
}

async function onCellClick(x, y) {
  if (!gameId) return;

  if (!legalSet.has(keyXY(x,y))) {
    setMessage("そこは合法手ではありません。白い点の場所に置いてください。");
    return;
  }

  const res = await fetch("/api/move", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({game_id: gameId, x, y})
  });

  const data = await res.json();
  if (!res.ok) {
    // invalid move etc
    setMessage(data.message || data.error || "エラー");
    if (data.board) updateUI(data);
    return;
  }

  updateUI(data);
  await refreshHistory();
}

function updateUI(state) {
  gameId = state.game_id;
  statusText.textContent = `Game #${gameId} / ${state.status}`;
  blackCountEl.textContent = state.black_count;
  whiteCountEl.textContent = state.white_count;
  turnTextEl.textContent = state.status === "active" ? jpPlayer(state.current_player) : "-";
  setMessage(state.message);

  renderBoard(state.board, state.legal_moves || []);

  if (state.status === "finished") {
    const w = state.winner;
    const winnerText = (w === "B") ? "黒の勝ち" : (w === "W") ? "白の勝ち" : "引き分け";
    setMessage(`終了：${winnerText}（黒 ${state.black_count} / 白 ${state.white_count}）`);
  }
}

async function newGame() {
  const res = await fetch("/api/new", { method: "POST" });
  const data = await res.json();
  updateUI(data);
  await refreshHistory();
}

async function refreshHistory() {
  const res = await fetch("/api/history");
  const data = await res.json();
  const history = data.history || [];

  historyListEl.innerHTML = "";

  if (history.length === 0) {
    const empty = document.createElement("div");
    empty.textContent = "まだ対戦履歴がありません。ゲームを終了させると記録されます。";
    empty.style.opacity = "0.85";
    historyListEl.appendChild(empty);
    return;
  }

  for (const h of history) {
    const item = document.createElement("div");
    item.className = "historyItem";
    item.innerHTML = `
      <div><b>Game #${h.game_id}</b> / 勝者: <b>${h.winner}</b></div>
      <div>黒 ${h.black_count} / 白 ${h.white_count} / 手数 ${h.total_moves}</div>
      <div style="opacity:.8;">${h.played_at}</div>
    `;
    historyListEl.appendChild(item);
  }
}

document.getElementById("newGameBtn").addEventListener("click", newGame);
document.getElementById("refreshHistoryBtn").addEventListener("click", refreshHistory);

// 起動時：新規ゲームを自動で開始
newGame().catch(err => {
  console.error(err);
  setMessage("起動エラー：コンソールを確認してください。");
});
