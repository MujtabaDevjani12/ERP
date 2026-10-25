from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Retired: the owner console is now a separate website with its own accounts. Use:  python manage.py create_owner you@example.com"

    def handle(self, *args, **options):
        raise CommandError("make_platform_owner is retired. The owner console has its own accounts now:\n"
                           "  python manage.py setup_owner --email you@example.com --name \"Your Name\"\n"
                           "  python manage.py runserver 8001 --settings=config.owner_settings")
