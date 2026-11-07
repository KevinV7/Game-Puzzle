from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Level(models.Model):
    """A difficulty level. The three rows are seeded by migration 0002."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=20)
    order = models.PositiveSmallIntegerField(unique=True)
    size = models.PositiveSmallIntegerField()
    time_limit_seconds = models.PositiveIntegerField()
    move_limit = models.PositiveIntegerField()
    multiplier = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.name


class Game(models.Model):
    """One attempt ("partida") of a user on a level."""

    ACTIVE = "active"
    WON = "won"
    LOST = "lost"
    STATUS_CHOICES = [(ACTIVE, "Activa"), (WON, "Ganada"), (LOST, "Perdida")]

    TIMEOUT = "timeout"
    MOVE_LIMIT = "move_limit"
    ABANDONED = "abandoned"
    LOSS_REASON_CHOICES = [
        (TIMEOUT, "Tiempo agotado"),
        (MOVE_LIMIT, "Límite de movimientos"),
        (ABANDONED, "Abandonada"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="games")
    level = models.ForeignKey(Level, on_delete=models.PROTECT, related_name="games")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=ACTIVE)
    loss_reason = models.CharField(max_length=12, choices=LOSS_REASON_CHOICES, blank=True, default="")
    initial_board = models.JSONField()
    board = models.JSONField()
    moves_used = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField()
    deadline = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    # Fixed at the winning move; null for every other status.
    seconds_remaining = models.PositiveIntegerField(null=True, blank=True)
    moves_remaining = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status="active"),
                name="one_active_game_per_user",
            ),
            models.CheckConstraint(
                name="game_state_is_consistent",
                condition=(
                    Q(
                        status="active",
                        ended_at__isnull=True,
                        loss_reason="",
                        seconds_remaining__isnull=True,
                        moves_remaining__isnull=True,
                    )
                    | Q(
                        status="won",
                        ended_at__isnull=False,
                        loss_reason="",
                        seconds_remaining__isnull=False,
                        moves_remaining__isnull=False,
                    )
                    | Q(
                        status="lost",
                        ended_at__isnull=False,
                        loss_reason__in=["timeout", "move_limit", "abandoned"],
                        seconds_remaining__isnull=True,
                        moves_remaining__isnull=True,
                    )
                ),
            ),
        ]
        indexes = [models.Index(fields=["user", "level", "status"], name="game_user_level_status_idx")]

    def __str__(self):
        return f"Game {self.pk} ({self.user_id}, {self.level_id}, {self.status})"

    @property
    def duration(self):
        """End minus start, or None while the game is still active."""
        if self.ended_at is None:
            return None
        return self.ended_at - self.started_at


class Move(models.Model):
    """One processed move request. Invalid moves are logged and counted too."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="moves")
    seq = models.PositiveIntegerField()
    tile = models.PositiveSmallIntegerField()
    valid = models.BooleanField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["seq"]
        constraints = [models.UniqueConstraint(fields=["game", "seq"], name="unique_move_seq_per_game")]
