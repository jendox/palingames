from django.apps import apps
from django.test.runner import DiscoverRunner


class PalingamesDiscoverRunner(DiscoverRunner):
    """Default Django test discovery excludes optional bot tests (need ``--extra bot``).

    Bot tests live under ``tests/bot/`` and are run separately:

        uv run python -m unittest discover -s tests/bot -v
    """

    def build_suite(self, test_labels=None, **kwargs):
        if not test_labels:
            test_labels = [
                app.name
                for app in apps.get_app_configs()
                if app.name.startswith("apps.")
            ]
            test_labels.append("tests.test_express_pay_client")
        return super().build_suite(test_labels, **kwargs)
