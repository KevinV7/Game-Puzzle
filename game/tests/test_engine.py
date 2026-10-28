from django.test import SimpleTestCase

from game import engine
from game.engine import InvalidBoard, InvalidMove

SIZES = (3, 4, 5)


def board_with_gap(size, gap_index):
    """Tiles 1..n*n-1 in ascending order with the gap inserted at gap_index."""
    tiles = list(range(1, size * size))
    tiles.insert(gap_index, 0)
    return tuple(tiles)


def expected_neighbors(board, size):
    """Edge-sharing neighbours of the gap, computed from (row, col) coordinates."""
    row, col = divmod(board.index(0), size)
    found = set()
    for d_row, d_col in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        r, c = row + d_row, col + d_col
        if 0 <= r < size and 0 <= c < size:
            found.add(board[r * size + c])
    return found


class BoardModelTests(SimpleTestCase):
    def test_goal_boards_hold_all_tiles_and_one_gap(self):
        for size in SIZES:
            with self.subTest(size=size):
                board = engine.solved_board(size)
                self.assertEqual(len(board), size * size)
                self.assertEqual(board.count(0), 1)
                self.assertEqual(
                    sorted(tile for tile in board if tile), list(range(1, size * size))
                )

    def test_goal_board_counts_per_size(self):
        for size, tiles in ((3, 8), (4, 15), (5, 24)):
            with self.subTest(size=size):
                board = engine.solved_board(size)
                self.assertEqual(len([t for t in board if t != 0]), tiles)

    def test_goal_layout_is_ascending_with_gap_last(self):
        self.assertEqual(engine.solved_board(3), (1, 2, 3, 4, 5, 6, 7, 8, 0))
        for size in SIZES:
            with self.subTest(size=size):
                board = engine.solved_board(size)
                self.assertEqual(board[-1], 0)
                self.assertEqual(board[:-1], tuple(range(1, size * size)))

    def test_goal_board_is_a_tuple(self):
        self.assertIsInstance(engine.solved_board(3), tuple)

    def test_unsupported_sizes_are_rejected(self):
        for size in (2, 6, 0, -3):
            with self.subTest(size=size):
                with self.assertRaises(InvalidBoard):
                    engine.solved_board(size)

    def test_validation_error_is_a_value_error(self):
        self.assertTrue(issubclass(InvalidBoard, ValueError))

    def test_valid_boards_pass_validation(self):
        for size in SIZES:
            with self.subTest(size=size):
                engine.validate_board(engine.solved_board(size), size)
                engine.validate_board(board_with_gap(size, 0), size)

    def test_invalid_board_content_is_rejected(self):
        invalid = {
            "duplicate tile": (1, 1, 3, 4, 5, 6, 7, 8, 0),
            "no gap": (1, 2, 3, 4, 5, 6, 7, 8, 9),
            "two gaps": (0, 0, 3, 4, 5, 6, 7, 8, 1),
            "8 cells": (1, 2, 3, 4, 5, 6, 7, 0),
            "10 cells": (1, 2, 3, 4, 5, 6, 7, 8, 9, 0),
            "tile out of range": (1, 2, 3, 4, 5, 6, 7, 9, 0),
            "negative tile": (1, 2, 3, 4, 5, 6, 7, -1, 0),
        }
        for name, board in invalid.items():
            with self.subTest(name):
                with self.assertRaises(InvalidBoard):
                    engine.validate_board(board, 3)

    def test_validation_rejects_unsupported_size(self):
        with self.assertRaises(InvalidBoard):
            engine.validate_board((1, 2, 3, 0), 2)


class SolvedDetectionTests(SimpleTestCase):
    def test_goal_board_is_solved(self):
        for size in SIZES:
            with self.subTest(size=size):
                self.assertTrue(engine.is_solved(engine.solved_board(size)))

    def test_one_move_away_is_not_solved(self):
        for size in SIZES:
            with self.subTest(size=size):
                goal = engine.solved_board(size)
                tile = goal[-2]
                moved = engine.apply_move(goal, size, tile)
                self.assertFalse(engine.is_solved(moved))

    def test_reverse_move_is_solved_again(self):
        for size in SIZES:
            with self.subTest(size=size):
                goal = engine.solved_board(size)
                tile = goal[-2]
                moved = engine.apply_move(goal, size, tile)
                self.assertEqual(engine.apply_move(moved, size, tile), goal)
                self.assertTrue(engine.is_solved(engine.apply_move(moved, size, tile)))

    def test_arbitrary_board_is_not_solved(self):
        self.assertFalse(engine.is_solved((2, 1, 3, 4, 5, 6, 7, 8, 0)))


class AdjacencyTests(SimpleTestCase):
    def test_gap_in_centre_has_four_neighbours(self):
        board = (1, 2, 3, 4, 0, 5, 6, 7, 8)
        self.assertEqual(engine.neighbors(board, 3), frozenset({2, 4, 5, 7}))

    def test_gap_in_top_left_corner_has_exactly_two_neighbours(self):
        for size in SIZES:
            with self.subTest(size=size):
                board = board_with_gap(size, 0)
                # Right of the gap is tile 1, below it is tile `size`. The diagonal
                # tile must not count.
                self.assertEqual(engine.neighbors(board, size), frozenset({1, size}))

    def test_no_wrap_around_across_rows(self):
        for size in SIZES:
            with self.subTest(size=size, edge="left"):
                # Gap at the start of the second row: the last tile of the first
                # row sits right before it in the flat tuple but is not adjacent.
                board = board_with_gap(size, size)
                self.assertNotIn(board[size - 1], engine.neighbors(board, size))
            with self.subTest(size=size, edge="right"):
                # Gap at the end of the first row: the first tile of the second row
                # follows it in the flat tuple but is not adjacent.
                board = board_with_gap(size, size - 1)
                self.assertNotIn(board[size], engine.neighbors(board, size))

    def test_every_gap_position_matches_the_coordinate_model(self):
        for size in SIZES:
            for gap_index in range(size * size):
                with self.subTest(size=size, gap_index=gap_index):
                    board = board_with_gap(size, gap_index)
                    self.assertEqual(
                        engine.neighbors(board, size), expected_neighbors(board, size)
                    )

    def test_neighbors_returns_a_frozenset(self):
        self.assertIsInstance(engine.neighbors(engine.solved_board(3), 3), frozenset)

    def test_neighbors_rejects_an_invalid_board(self):
        with self.assertRaises(InvalidBoard):
            engine.neighbors((1, 1, 3, 4, 5, 6, 7, 8, 0), 3)


class MoveApplicationTests(SimpleTestCase):
    def test_valid_move_swaps_tile_and_gap(self):
        board = (1, 2, 3, 4, 5, 6, 7, 0, 8)
        self.assertEqual(
            engine.apply_move(board, 3, 8), (1, 2, 3, 4, 5, 6, 7, 8, 0)
        )

    def test_valid_move_keeps_a_permutation(self):
        board = (1, 2, 3, 4, 0, 5, 6, 7, 8)
        for tile in engine.neighbors(board, 3):
            with self.subTest(tile=tile):
                result = engine.apply_move(board, 3, tile)
                self.assertEqual(sorted(result), sorted(board))

    def test_non_adjacent_tile_is_an_invalid_move(self):
        board = engine.solved_board(3)
        snapshot = tuple(board)
        with self.assertRaises(InvalidMove):
            engine.apply_move(board, 3, 1)
        self.assertEqual(board, snapshot)

    def test_diagonal_tile_is_an_invalid_move(self):
        board = (1, 2, 3, 4, 0, 5, 6, 7, 8)
        with self.assertRaises(InvalidMove):
            engine.apply_move(board, 3, 1)

    def test_unknown_tiles_are_invalid_moves(self):
        board = engine.solved_board(3)
        snapshot = tuple(board)
        for tile in (99, 0, -1, 9):
            with self.subTest(tile=tile):
                with self.assertRaises(InvalidMove):
                    engine.apply_move(board, 3, tile)
                self.assertEqual(board, snapshot)

    def test_input_board_is_not_mutated(self):
        board = (1, 2, 3, 4, 5, 6, 7, 0, 8)
        snapshot = tuple(board)
        result = engine.apply_move(board, 3, 8)
        self.assertEqual(board, snapshot)
        self.assertIsNot(result, board)
        self.assertIsInstance(result, tuple)


class TileInPlaceTests(SimpleTestCase):
    def test_tile_in_goal_position_is_in_place(self):
        board = engine.solved_board(3)
        for tile in range(1, 9):
            with self.subTest(tile=tile):
                self.assertTrue(engine.tile_in_place(board, tile))

    def test_tile_out_of_position_is_not_in_place(self):
        board = (1, 2, 3, 4, 5, 6, 7, 0, 8)
        self.assertTrue(engine.tile_in_place(board, 1))
        self.assertFalse(engine.tile_in_place(board, 8))

    def test_swapped_tiles_are_not_in_place(self):
        board = (2, 1, 3, 4, 5, 6, 7, 8, 0)
        self.assertFalse(engine.tile_in_place(board, 1))
        self.assertFalse(engine.tile_in_place(board, 2))
        self.assertTrue(engine.tile_in_place(board, 3))

    def test_unknown_tile_is_rejected(self):
        board = engine.solved_board(3)
        for tile in (0, 9, 99):
            with self.subTest(tile=tile):
                with self.assertRaises(InvalidMove):
                    engine.tile_in_place(board, tile)
