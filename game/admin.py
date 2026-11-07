from django.contrib import admin

from game.models import Game, Level, Move


class ReadOnlyAdmin(admin.ModelAdmin):
    """Admin that only lists and shows rows.

    Progression is derived from won games, so they must never change or disappear.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Level)
class LevelAdmin(ReadOnlyAdmin):
    list_display = ("name", "order", "size", "time_limit_seconds", "move_limit", "multiplier")


@admin.register(Game)
class GameAdmin(ReadOnlyAdmin):
    list_display = ("id", "user", "level", "status", "loss_reason", "moves_used", "started_at", "ended_at")
    list_filter = ("status", "level")


@admin.register(Move)
class MoveAdmin(ReadOnlyAdmin):
    list_display = ("game", "seq", "tile", "valid", "created_at")
