"""Pure sliding-puzzle rules: board model, adjacency, moves and solved detection.

This module depends on the standard library only. It has no database, HTTP, clock
or Django dependency, so it can be imported and tested in isolation.

A board is a flat tuple of ints in row-major order. The tiles ``1 .. size*size-1``
appear once each and ``0`` is the gap. The goal layout is the tiles in ascending
order with the gap in the last cell.
"""

from math import isqrt

SUPPORTED_SIZES = (3, 4, 5)
GAP = 0

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
