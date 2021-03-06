import mock
from datetime import datetime, timezone, timedelta

from django.core.management import call_command
from django.conf import settings
from django.test import TestCase
from stmoab.ExperimentDashboard import ExperimentDashboard

from experimenter.experiments.models import Experiment
from experimenter.experiments.tests.factories import ExperimentFactory
from experimenter.experiments.management.commands.generate_dashboards import (
    DASHBOARD_TAG_NAME,
    sanitize_name,
)


class GenerateDashboardsTest(TestCase):

    def setUp(self):
        self.ORIGINAL_EXTERNAL_API_EXCEPTION = (
            ExperimentDashboard.ExternalAPIError
        )

        dashboard_patcher = mock.patch(
            (
                "experimenter.experiments.management.commands."
                "generate_dashboards.ExperimentDashboard"
            )
        )
        logging_patcher = mock.patch(
            (
                "experimenter.experiments.management.commands."
                "generate_dashboards.logging"
            )
        )
        self.mock_logger = logging_patcher.start()
        self.MockDashboard = dashboard_patcher.start()
        self.MockDashboard.ExternalAPIError = (
            self.ORIGINAL_EXTERNAL_API_EXCEPTION
        )
        self.addCleanup(logging_patcher.stop)
        self.addCleanup(dashboard_patcher.stop)

        # A draft experiment with no dashboard should be ignored
        self.experiment_draft = ExperimentFactory.create_with_status(
            Experiment.STATUS_DRAFT, dashboard_url=None
        )

        # A review experiment with no dashboard should be ignored
        self.experiment_review = ExperimentFactory.create_with_status(
            Experiment.STATUS_REVIEW, dashboard_url=None
        )

        # An accepted experiment with no dashboard should be ignored
        self.experiment_review = ExperimentFactory.create_with_status(
            Experiment.STATUS_ACCEPTED, dashboard_url=None
        )

        # A rejected experiment with no dashboard should be ignored
        self.experiment_review = ExperimentFactory.create_with_status(
            Experiment.STATUS_REJECTED, dashboard_url=None
        )

        # A launched experiment should be ignored
        self.experiment_launched = ExperimentFactory.create_with_status(
            Experiment.STATUS_LIVE
        )

        # An old experiment with a missing dashboard should be ignored
        self.experiment_missing_dash = ExperimentFactory.create_with_status(
            Experiment.STATUS_COMPLETE
        )
        self.experiment_missing_dash.dashboard_url = None
        self.experiment_missing_dash.save()

        self.experiments = []

        for i in range(3):
            # A recently complete experiment
            experiment_complete = ExperimentFactory.create_with_status(
                Experiment.STATUS_COMPLETE
            )

            relevant_change = experiment_complete.changes.filter(
                old_status=Experiment.STATUS_LIVE,
                new_status=Experiment.STATUS_COMPLETE,
            ).get()

            relevant_change.changed_on = datetime.now(
                timezone.utc
            ) - timedelta(days=1)

            relevant_change.save()

            self.experiments.append(experiment_complete)

    def test_dashboard_object_generated(self):
        expected_call_args = [
            (
                settings.REDASH_API_KEY,
                DASHBOARD_TAG_NAME,
                sanitize_name(experiment.name),
                experiment.slug,
                experiment.start_date.strftime("%Y-%m-%d"),
                (
                    None
                    if not experiment.end_date
                    else experiment.end_date.strftime("%Y-%m-%d")
                ),
            )
            for experiment in self.experiments
        ]

        DEFAULT_PUBLIC_URL = "http://www.example.com/dashboard"
        NEW_PUBLIC_URL = "www.example.com/some_dashboard_url"

        mock_instance = self.MockDashboard.return_value
        mock_instance.slug_url = NEW_PUBLIC_URL
        mock_instance.UT_HOURLY_EVENTS = [1, 2, 3, 4]
        mock_instance.MAPPED_UT_EVENTS = [1, 2, 3]
        mock_instance.get_update_range.return_value = {
            "min": datetime.now() - timedelta(days=2)
        }
        mock_instance.get_query_ids_and_names.return_value = [
            i for i in range(3)
        ]

        with self.settings(DASHBOARD_RATE_LIMIT=len(self.experiments)):
            call_command("generate_dashboards")

            self.assertTrue(self.MockDashboard.called)
            self.assertEqual(
                self.MockDashboard.call_count, len(self.experiments)
            )

            for idx, call_args in enumerate(self.MockDashboard.call_args_list):
                args, kwargs = call_args
                self.assertEqual(args, expected_call_args[idx])

            # The dashboards were not complete, so dashboard_url is not set
            for experiment in self.experiments:
                experiment_obj = Experiment.objects.get(pk=experiment.pk)
                self.assertTrue(
                    experiment_obj.dashboard_url in [None, DEFAULT_PUBLIC_URL]
                )

            self.assertEqual(
                len(mock_instance.add_graph_templates.mock_calls),
                5 * len(self.experiments),
            )
            self.assertEqual(
                len(mock_instance.get_update_range.mock_calls),
                len(self.experiments),
            )

            self.call_count = 0

            def get_widgets():
                num_widgets = 3
                if self.call_count % 2 != 0:
                    num_widgets = 7
                self.call_count += 1
                widgets = [i for i in range(num_widgets)]
                return widgets

            mock_instance.get_query_ids_and_names.side_effect = get_widgets

            call_command("generate_dashboards")

            # The dashboards are now complete, so dashboard_url is set
            self.assertEqual(
                len(mock_instance.add_graph_templates.mock_calls),
                10 * len(self.experiments),
            )
            for experiment in self.experiments:
                experiment_obj = Experiment.objects.get(pk=experiment.pk)
                self.assertEqual(experiment_obj.dashboard_url, NEW_PUBLIC_URL)

    def test_recently_updated_dashboard_is_ignored(self):
        mock_instance = self.MockDashboard.return_value
        mock_instance.slug_url = "www.example.com/dashboard"
        mock_instance.get_update_range.return_value = {
            "min": datetime.now(timezone.utc)
        }
        mock_instance.get_query_ids_and_names.return_value = [
            i for i in range(int(15 / 2))
        ]

        with self.settings(DASHBOARD_RATE_LIMIT=len(self.experiments)):
            call_command("generate_dashboards")

            self.assertTrue(self.MockDashboard.called)
            self.assertEqual(self.MockDashboard.call_count, 3)

            self.assertEqual(
                len(mock_instance.add_graph_templates.mock_calls), 0
            )

    def test_dashboards_are_rate_limited(self):
        mock_instance = self.MockDashboard.return_value
        mock_instance.slug_url = "www.example.com/dashboard"
        mock_instance.get_update_range.return_value = {
            "min": datetime.now(timezone.utc)
        }
        mock_instance.get_query_ids_and_names.return_value = [
            i for i in range(int(15 / 2))
        ]

        rate_limit = len(self.experiments) - 1
        with self.settings(DASHBOARD_RATE_LIMIT=rate_limit):
            call_command("generate_dashboards")

            self.assertTrue(self.MockDashboard.called)
            self.assertEqual(self.MockDashboard.call_count, rate_limit)

    def test_external_api_error_is_caught(self):
        ERROR_MESSAGE = "Unable to communicate with Redash"

        self.MockDashboard.side_effect = ExperimentDashboard.ExternalAPIError(
            ERROR_MESSAGE
        )

        call_command("generate_dashboards")

        self.assertTrue(self.mock_logger.error.called)

    def test_value_error_is_caught(self):
        ERROR_MESSAGE = "column_mapping value required"

        self.MockDashboard.side_effect = ValueError(ERROR_MESSAGE)

        call_command("generate_dashboards")

        self.assertTrue(self.mock_logger.error.called)
