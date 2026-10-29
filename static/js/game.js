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
