from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower


class User(AbstractUser):
    """Project user. Defined from day one so later models can reference it safely."""

    email = models.EmailField("correo electrónico", unique=True)

    class Meta(AbstractUser.Meta):
        # Backstop for the case-insensitive rule: A@x.com and a@x.com collide.
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
        ]
