"""Pure sliding-puzzle rules: board model, adjacency, moves and solved detection.

This module depends on the standard library only. It has no database, HTTP, clock
or Django dependency, so it can be imported and tested in isolation.

A board is a flat tuple of ints in row-major order. The tiles ``1 .. size*size-1``
appear once each and ``0`` is the gap. The goal layout is the tiles in ascending
order with the gap in the last cell.
"""

import random
from math import isqrt
from types import MappingProxyType

SUPPORTED_SIZES = (3, 4, 5)
GAP = 0
# Length of the shuffle random walk per board size.
DEFAULT_STEPS = MappingProxyType({3: 150, 4: 300, 5: 500})

Board = tuple[int, ...]


class InvalidBoard(ValueError):
    """The size is unsupported or the cells are not a valid board."""


class InvalidMove(Exception):
    """The tile is not on the board or does not share an edge with the gap."""


def validate_board(board: Board, size: int) -> None:
    """Raise InvalidBoard unless ``board`` is a valid board of ``size``."""
    if size not in SUPPORTED_SIZES:
        raise InvalidBoard(f"unsupported size: {size!r}")
    if len(board) != size * size:
        raise InvalidBoard(f"expected {size * size} cells, got {len(board)}")
    if sorted(board) != list(range(size * size)):
        raise InvalidBoard("a board holds each tile once plus exactly one gap")


def solved_board(size: int) -> Board:
    """Return the goal layout of ``size``."""
    if size not in SUPPORTED_SIZES:
        raise InvalidBoard(f"unsupported size: {size!r}")
    return (*range(1, size * size), GAP)


def is_solved(board: Board) -> bool:
    """Return True if and only if ``board`` equals the goal layout of its size."""
    size = isqrt(len(board))
    return size in SUPPORTED_SIZES and tuple(board) == solved_board(size)


def neighbors(board: Board, size: int) -> frozenset[int]:
    """Return the tiles that share an edge with the gap (the movable tiles)."""
    validate_board(board, size)
    return frozenset(board[i] for i in _adjacent_cells(board.index(GAP), size))


def apply_move(board: Board, size: int, tile: int) -> Board:
    """Return a new board with ``tile`` swapped into the gap.

    Raises InvalidMove if the tile is not on the board or not adjacent to the gap.
    The input board is never mutated.
    """
    validate_board(board, size)
    gap = board.index(GAP)
    if tile == GAP or tile not in board:
        raise InvalidMove(f"tile {tile!r} is not on the board")
    position = board.index(tile)
    if position not in _adjacent_cells(gap, size):
        raise InvalidMove(f"tile {tile!r} is not next to the gap")
    return _swap(board, gap, position)


def tile_in_place(board: Board, tile: int) -> bool:
    """Return True if ``tile`` sits in its goal cell.

    Raises InvalidMove if ``tile`` is not a numbered tile of the board.
    """
    if not 1 <= tile < len(board) or tile not in board:
        raise InvalidMove(f"tile {tile!r} is not on the board")
    return board.index(tile) == tile - 1


def is_solvable(board: Board) -> bool:
    """Return True if ``board`` can reach the goal layout by legal moves.

    Decided by inversion parity, independently of how the board was produced. The
    goal has the gap in the last cell, so for an odd width the number of inversions
    must be even; for an even width the inversions plus the gap's row counted from
    the bottom (1-based) must be odd.
    """
    size = isqrt(len(board))
    validate_board(board, size)
    tiles = [tile for tile in board if tile != GAP]
    inversions = sum(
        1
        for i, tile in enumerate(tiles)
        for later in tiles[i + 1 :]
        if tile > later
    )
    if size % 2 == 1:
        return inversions % 2 == 0
    gap_row_from_bottom = size - board.index(GAP) // size
    return (inversions + gap_row_from_bottom) % 2 == 1


def shuffle(size: int, rng: random.Random, steps: int | None = None) -> Board:
    """Return a solvable, unsolved board reached by a random walk from the goal.

    Every step slides one tile into the gap, so the result is solvable by
    construction. A step never undoes the previous one. It draws one
    ``rng.choice`` per step, from a sorted list of candidate tiles, so the same
    seed always yields the same board. A walk that ends on the goal is redrawn.
    ``steps`` defaults to ``DEFAULT_STEPS[size]``.
    """
    if size not in SUPPORTED_SIZES:
        raise InvalidBoard(f"unsupported size: {size!r}")
    if steps is None:
        steps = DEFAULT_STEPS[size]
    if steps < 1:
        raise ValueError("steps must be at least 1")
    while True:
        board = _random_walk(size, rng, steps)
        if not is_solved(board):
            return board


def _random_walk(size: int, rng: random.Random, steps: int) -> Board:
    cells = list(solved_board(size))
    gap = cells.index(GAP)
    previous_tile = None
    for _ in range(steps):
        candidates = sorted(
            cells[i] for i in _adjacent_cells(gap, size) if cells[i] != previous_tile
        )
        tile = rng.choice(candidates)
        target = cells.index(tile)
        cells[gap], cells[target] = tile, GAP
        gap = target
        previous_tile = tile
    return tuple(cells)


def _adjacent_cells(index: int, size: int) -> list[int]:
    """Indexes of the cells sharing an edge with ``index``: no diagonals, no wrap."""
    row, col = divmod(index, size)
    cells = []
    if row > 0:
        cells.append(index - size)
    if row < size - 1:
        cells.append(index + size)
    if col > 0:
        cells.append(index - 1)
    if col < size - 1:
        cells.append(index + 1)
    return cells


def _swap(board: Board, i: int, j: int) -> Board:
    cells = list(board)
    cells[i], cells[j] = cells[j], cells[i]
    return tuple(cells)
