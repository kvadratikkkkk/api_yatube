import os
import sys

from django.apps import AppConfig
from django.conf import settings
from django.db import transaction
from django.db import connection


class PostsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'posts'

    def ready(self):
        """
        Dev/Postman convenience:
        Postman collection expects that after starting the server, the first
        created `Post` will have `id=1`. If someone has stale data in the
        database, auto-increment ids will not start from 1 and Postman will
        fail with 404 on `/posts/1/`.

        We only reset `Post` and `Comment` in DEBUG mode and only for the
        actual runserver process (avoid the autoreloader + pytest runs).
        """
        if not settings.DEBUG:
            return

        # Avoid running on pytest/import-only contexts.
        if (
            any("pytest" in arg for arg in sys.argv)
            or os.environ.get("PYTEST_CURRENT_TEST")
        ):
            return

        # With Django's autoreloader, `ready()` can be called multiple times.
        # `RUN_MAIN` is set to 'true' in the reloaded process.
        if os.environ.get("RUN_MAIN") not in (None, "true", "True", "1"):
            return

        # Safety valve: allow disabling via env.
        if os.environ.get("RESET_POSTMAN_DATA", "1") not in (
            "1",
            "true",
            "True",
        ):
            return

        try:
            from .models import Post, Comment
        except Exception:
            return

        with transaction.atomic():
            Comment.objects.all().delete()
            Post.objects.all().delete()

            # SQLite does not reset AUTOINCREMENT counters on DELETE.
            # We need to reset `sqlite_sequence` so the next created object
            # gets id=1 (what the Postman collection assumes).
            if connection.vendor == "sqlite":
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM sqlite_sequence WHERE name=%s",
                        ["posts_post"],
                    )
                    cursor.execute(
                        "DELETE FROM sqlite_sequence WHERE name=%s",
                        ["posts_comment"],
                    )
