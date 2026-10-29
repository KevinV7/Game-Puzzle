// Interactive board for the sliding puzzle (a plain ES module, no bundler).
//
// The server is the only authority. This script sends a tile number and the expected
// move number, then draws whatever board the server answers with. It never decides
// whether a move is valid, nor whether the game is won or lost.
//
// Everything above the "Page wiring" section is a pure function with no access to the
// DOM, so it can be exercised on its own.

export const SWIPE_THRESHOLD = 24; // px a pointer must travel to count as a swipe
export const NOTICE_MS = 3000; // how long the loss notice stays on screen

const DIRECTION_BY_KEY = new Map([
  ["ArrowUp", "up"],
  ["ArrowDown", "down"],
  ["ArrowLeft", "left"],
  ["ArrowRight", "right"],
]);

const LOSS_MESSAGES = new Map([
  ["timeout", "Perdiste: tiempo agotado"],
  ["move_limit", "Perdiste: límite de movimientos"],
]);

/** The slide direction an arrow key stands for, or null for any other key. */
export function directionForKey(key) {
  return DIRECTION_BY_KEY.get(key) ?? null;
}

/**
 * The number of the tile that slides in `direction` into the gap, or null when there
 * is none (the gap sits against that wall). ArrowUp slides the tile below the gap up.
 * `board` is the row-major list the server sends, with 0 for the gap.
 */
export function tileForDirection(board, size, direction) {
  const gap = board.indexOf(0);
  if (gap < 0) return null;
  const row = Math.floor(gap / size);
  const col = gap % size;
  let index = -1;
  if (direction === "up" && row < size - 1) index = gap + size;
  else if (direction === "down" && row > 0) index = gap - size;
  else if (direction === "left" && col < size - 1) index = gap + 1;
  else if (direction === "right" && col > 0) index = gap - 1;
  return index < 0 ? null : board[index];
}

/** The slide direction of a pointer that travelled (dx, dy), or null for a tap. */
export function swipeDirection(dx, dy, threshold = SWIPE_THRESHOLD) {
  const horizontal = Math.abs(dx);
  const vertical = Math.abs(dy);
  if (Math.max(horizontal, vertical) < threshold) return null;
  if (horizontal > vertical) return dx > 0 ? "right" : "left";
  return dy > 0 ? "down" : "up";
}

/** mm:ss for a number of seconds, never negative. */
export function formatClock(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const minutes = String(Math.floor(total / 60)).padStart(2, "0");
  return `${minutes}:${String(total % 60).padStart(2, "0")}`;
}

/** Whole seconds left until `deadline` (both in ms on the same clock), never below 0. */
export function secondsLeft(deadline, now) {
  return Math.max(0, Math.ceil((deadline - now) / 1000));
}

/** The loss notice for a loss reason, or null when none is shown (e.g. `abandoned`). */
export function lossMessage(reason) {
  return LOSS_MESSAGES.get(reason) ?? null;
}

/**
 * What the win panel shows for a move or state response that reports a win.
 * `action` is "next" (a POST to start `nextLevel`) or "levels" (a link to the level
 * selection). The panel never shows a score.
 */
export function describeWin(payload) {
  const { game } = payload;
  const stats = `Movimientos usados: ${game.moves_used} de ${game.move_limit}. Tiempo restante: ${formatClock(game.seconds_remaining)}.`;
  if (payload.completed_all) {
    return {
      title: "¡Completaste todos los niveles!",
      text: `Ya puedes jugar cualquier nivel. ${stats}`,
      action: "levels",
      nextLevel: null,
    };
  }
  if (payload.next_level) {
    return { title: "¡Nivel superado!", text: stats, action: "next", nextLevel: payload.next_level };
  }
  return { title: "¡Nivel superado!", text: stats, action: "levels", nextLevel: null };
}

/** Put a game id into a URL the server rendered for the placeholder id 0. */
export function gameUrl(template, id) {
  return template.replace("/0/", `/${id}/`);
}

/** Put a level slug into the start URL the server rendered with `__slug__`. */
export function startUrl(template, slug) {
  return template.replace("__slug__", slug);
}

/** The value of cookie `name` in a `document.cookie` string, or "". */
export function readCookie(cookies, name) {
  for (const part of cookies.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return "";
}

// Hook for the sounds added in a later change. Listeners receive one cue name:
// "active", "slide", "correct", "incorrect", "invalid", "win" or "lose".
const cueListeners = [];

export function onCue(listener) {
  cueListeners.push(listener);
}

export function emitCue(name) {
  for (const listener of cueListeners) {
    try {
      listener(name);
    } catch {
      // A failing listener must never break the game.
    }
  }
}

// Page wiring

const TICK_MS = 250;
const RETRY_MS = 2000; // wait before asking the server again after a failed request
const EXPIRY_RECHECK_MS = 1000; // spacing of state requests while the countdown sits at 0
const OFFLINE_MESSAGE = "No se pudo conectar con el servidor. Reintentando…";

/** Wire the board page: `doc` and `win` are the document and window (injectable). */
export function start(doc, win) {
  const boardEl = doc.getElementById("board");
  const stateNode = doc.getElementById("game-state");
  if (!boardEl || !stateNode) return;

  const urls = {
    state: boardEl.dataset.stateUrl,
    move: boardEl.dataset.moveUrl,
    start: boardEl.dataset.startUrl,
    levels: boardEl.dataset.levelsUrl,
    login: boardEl.dataset.loginUrl,
  };
  const movesEl = doc.getElementById("moves");
  const timeEl = doc.getElementById("time");
  const noticeEl = doc.getElementById("notice");

  let game = null; // the attempt on screen, in the server's shape
  let shownGameId = null;
  let renderedSignature = "";
  let localDeadline = 0; // performance.now() value at which the countdown reaches 0
  let inFlight = false; // one request at a time
  let queued = null; // at most one input waits for the request in flight
  let notice = null; // null, "offline" or "loss"
  let lastExpiryCheck = -Infinity;

  // Notice

  function setNotice(kind, text) {
    notice = kind;
    noticeEl.textContent = text;
    noticeEl.hidden = false;
  }

  function clearNotice() {
    notice = null;
    noticeEl.textContent = "";
    noticeEl.hidden = true;
  }

  // Rendering: always the server's board

  function tileCell(number) {
    const cell = doc.createElement("li");
    cell.className = "tile";
    const button = doc.createElement("button");
    button.type = "button";
    button.className = "tile-button";
    button.dataset.tile = String(number);
    button.setAttribute("aria-label", `Ficha ${number}`);
    button.textContent = String(number);
    cell.append(button);
    return cell;
  }

  function gapCell() {
    const cell = doc.createElement("li");
    cell.className = "tile tile-gap";
    return cell;
  }

  function renderBoard(g) {
    const signature = `${g.size}:${g.board.join(",")}`;
    if (signature === renderedSignature) return;
    renderedSignature = signature;
    const focused = boardEl.contains(doc.activeElement) ? doc.activeElement.dataset.tile : undefined;
    boardEl.style.setProperty("--size", String(g.size));
    boardEl.setAttribute("aria-label", `Tablero ${g.size}x${g.size}`);
    boardEl.replaceChildren(...g.board.map((cell) => (cell === 0 ? gapCell() : tileCell(cell))));
    if (focused) boardEl.querySelector(`[data-tile="${focused}"]`)?.focus();
  }

  // Show an attempt. `receivedAt` is when the server said how many seconds were left,
  // so a delayed render (after the loss notice) still shows the server's clock.
  function adopt(g, receivedAt) {
    game = g;
    renderBoard(g);
    movesEl.textContent = `${g.moves_used} / ${g.move_limit}`;
    localDeadline = receivedAt + g.seconds_remaining * 1000;
    const seconds = g.status === "active" ? secondsLeft(localDeadline, performance.now()) : g.seconds_remaining;
    timeEl.textContent = formatClock(seconds);
    if (g.status === "active" && g.id !== shownGameId) emitCue("active");
    shownGameId = g.id;
  }

  // Countdown: display only. At 0 the server is asked and its verdict is shown.

  function tick() {
    if (!game || game.status !== "active" || notice === "loss") return;
    const left = secondsLeft(localDeadline, performance.now());
    timeEl.textContent = formatClock(left);
    if (left === 0) checkExpiry();
  }

  function checkExpiry() {
    const now = performance.now();
    if (inFlight || now - lastExpiryCheck < EXPIRY_RECHECK_MS) return;
    lastExpiryCheck = now;
    sync();
  }

  win.setInterval(tick, TICK_MS);
  doc.addEventListener("visibilitychange", () => {
    if (!doc.hidden && canPlay()) sync();
    tick();
  });

  // Request pipeline: one request in flight, at most one input queued behind it.

  function canPlay() {
    return game !== null && game.status === "active" && notice !== "loss";
  }

  function goToLogin() {
    win.location.assign(`${urls.login}?next=${encodeURIComponent(win.location.pathname)}`);
  }

  async function call(url, init) {
    let response;
    try {
      response = await fetch(url, {
        credentials: "same-origin",
        ...init,
        headers: { Accept: "application/json", "X-CSRFToken": readCookie(doc.cookie, "csrftoken"), ...init.headers },
      });
    } catch {
      return { kind: "offline" };
    }
    const isJson = (response.headers.get("Content-Type") ?? "").includes("application/json");
    // The anonymous API answers with a redirect to the login page (or an HTML error).
    if (response.redirected || (!isJson && response.status < 500)) return { kind: "login" };
    if (!isJson) return { kind: "offline" };
    try {
      return { kind: "reply", status: response.status, data: await response.json() };
    } catch {
      return { kind: "offline" };
    }
  }

  async function sync() {
    if (inFlight) return;
    inFlight = true;
    settle(await call(gameUrl(urls.state, game.id), { method: "GET" }), true);
  }

  async function send(tile) {
    inFlight = true;
    const body = JSON.stringify({ tile, seq: game.moves_used + 1 });
    settle(await call(gameUrl(urls.move, game.id), { method: "POST", headers: { "Content-Type": "application/json" }, body }), false);
  }

  // Handle a finished request. 200 and 409 both carry the server's state, which wins.
  function settle(result, wasSync) {
    if (result.kind === "login") {
      goToLogin();
      return;
    }
    if (result.kind === "offline" || (wasSync && !result.data?.game && result.status !== 404)) {
      retryLater();
      return;
    }
    if (result.status === 404) {
      win.location.assign(urls.levels);
      return;
    }
    inFlight = false;
    if (!result.data?.game) {
      sync(); // a rejected request says nothing about the board: ask for it
      return;
    }
    apply(result.data, performance.now());
    drain();
  }

  // The request stays "in flight" while waiting, so input queues instead of piling up.
  function retryLater() {
    if (notice !== "loss") setNotice("offline", OFFLINE_MESSAGE);
    win.setTimeout(() => {
      inFlight = false;
      sync();
    }, RETRY_MS);
  }

  function emitMoveCues(move) {
    if (!move.valid) {
      emitCue("invalid");
      return;
    }
    emitCue("slide");
    emitCue(move.tile_in_place ? "correct" : "incorrect");
  }

  function apply(data, receivedAt) {
    if (notice === "offline") clearNotice();
    adopt(data.game, receivedAt);
    if (data.move) emitMoveCues(data.move);
  }

  // An input is a tile number (click) or a slide direction (key, swipe). A direction
  // is turned into a tile number only when it is sent, from the board then held.
  function submit(intent) {
    if (!canPlay()) return;
    if (inFlight) {
      queued ??= intent;
      return;
    }
    dispatch(intent);
  }

  function dispatch(intent) {
    const tile = intent.tile ?? tileForDirection(game.board, game.size, intent.direction);
    if (tile !== null && tile !== undefined) send(tile); // a wall sends nothing
  }

  function drain() {
    const next = queued;
    queued = null;
    if (next && canPlay() && !inFlight) dispatch(next);
  }

  adopt(JSON.parse(stateNode.textContent).game, performance.now());
}

if (typeof document !== "undefined") start(document, window);
