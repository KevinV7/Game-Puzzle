from django.db import migrations

# Values are hardcoded on purpose: this migration must not import app code that may
# change later. (slug, name, order, size, time_limit_seconds, move_limit, multiplier)
LEVELS = [
    ("facil", "Fácil", 1, 3, 180, 300, 1),
    ("normal", "Normal", 2, 4, 420, 700, 2),
    ("dificil", "Difícil", 3, 5, 720, 1500, 3),
]


def seed_levels(apps, schema_editor):
    Level = apps.get_model("game", "Level")
    for slug, name, order, size, time_limit_seconds, move_limit, multiplier in LEVELS:
        Level.objects.update_or_create(
            slug=slug,
            defaults={
                "name": name,
                "order": order,
                "size": size,
                "time_limit_seconds": time_limit_seconds,
                "move_limit": move_limit,
                "multiplier": multiplier,
            },
        )


def unseed_levels(apps, schema_editor):
    Level = apps.get_model("game", "Level")
    Level.objects.filter(slug__in=[row[0] for row in LEVELS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("game", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_levels, unseed_levels),
    ]
