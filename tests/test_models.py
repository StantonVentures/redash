#encoding: utf8
import datetime
import json
from unittest import TestCase

import mock
from dateutil.parser import parse as date_parse
from tests import BaseTestCase

from redash import models
from redash.models import db
from redash.utils import gen_query_hash, utcnow


class DashboardTest(BaseTestCase):
    def test_appends_suffix_to_slug_when_duplicate(self):
        d1 = self.factory.create_dashboard()
        db.session.flush()
        self.assertEquals(d1.slug, 'test')

        d2 = self.factory.create_dashboard(user=d1.user)
        db.session.flush()
        self.assertNotEquals(d1.slug, d2.slug)

        d3 = self.factory.create_dashboard(user=d1.user)
        db.session.flush()
        self.assertNotEquals(d1.slug, d3.slug)
        self.assertNotEquals(d2.slug, d3.slug)


class ShouldScheduleNextTest(TestCase):
    def test_interval_schedule_that_needs_reschedule(self):
        now = utcnow()
        two_hours_ago = now - datetime.timedelta(hours=2)
        self.assertTrue(models.should_schedule_next(two_hours_ago, now, "3600",
                                                    0))

    def test_interval_schedule_that_doesnt_need_reschedule(self):
        now = utcnow()
        half_an_hour_ago = now - datetime.timedelta(minutes=30)
        self.assertFalse(models.should_schedule_next(half_an_hour_ago, now,
                                                     "3600", 0))

    def test_exact_time_that_needs_reschedule(self):
        now = utcnow()
        yesterday = now - datetime.timedelta(days=1)
        scheduled_datetime = now - datetime.timedelta(hours=3)
        scheduled_time = "{:02d}:00".format(scheduled_datetime.hour)
        self.assertTrue(models.should_schedule_next(yesterday, now,
                                                    scheduled_time, 0))

    def test_exact_time_that_doesnt_need_reschedule(self):
        now = date_parse("2015-10-16 20:10")
        yesterday = date_parse("2015-10-15 23:07")
        schedule = "23:00"
        self.assertFalse(models.should_schedule_next(yesterday, now, schedule,
                                                     0))

    def test_exact_time_with_day_change(self):
        now = utcnow().replace(hour=0, minute=1)
        previous = (now - datetime.timedelta(days=2)).replace(hour=23,
                                                              minute=59)
        schedule = "23:59".format(now.hour + 3)
        self.assertTrue(models.should_schedule_next(previous, now, schedule,
                                                    0))

    def test_backoff(self):
        now = utcnow()
        two_hours_ago = now - datetime.timedelta(hours=2)
        self.assertTrue(models.should_schedule_next(two_hours_ago, now, "3600",
                                                    5))
        self.assertFalse(models.should_schedule_next(two_hours_ago, now,
                                                     "3600", 10))


class QueryOutdatedQueriesTest(BaseTestCase):
    # TODO: this test can be refactored to use mock version of should_schedule_next to simplify it.
    def test_outdated_queries_skips_unscheduled_queries(self):
        query = self.factory.create_query(schedule=None)
        queries = models.Query.outdated_queries()

        self.assertNotIn(query, queries)

    def test_outdated_queries_works_with_ttl_based_schedule(self):
        two_hours_ago = utcnow() - datetime.timedelta(hours=2)
        query = self.factory.create_query(schedule="3600")
        query_result = self.factory.create_query_result(query=query.query_text, retrieved_at=two_hours_ago)
        query.latest_query_data = query_result

        queries = models.Query.outdated_queries()
        self.assertIn(query, queries)

    def test_outdated_queries_works_scheduled_queries_tracker(self):
        two_hours_ago = datetime.datetime.now() - datetime.timedelta(hours=2)
        query = self.factory.create_query(schedule="3600")
        query_result = self.factory.create_query_result(query=query, retrieved_at=two_hours_ago)
        query.latest_query_data = query_result

        models.scheduled_queries_executions.update(query.id)

        queries = models.Query.outdated_queries()
        self.assertNotIn(query, queries)

    def test_skips_fresh_queries(self):
        half_an_hour_ago = utcnow() - datetime.timedelta(minutes=30)
        query = self.factory.create_query(schedule="3600")
        query_result = self.factory.create_query_result(query=query.query_text, retrieved_at=half_an_hour_ago)
        query.latest_query_data = query_result

        queries = models.Query.outdated_queries()
        self.assertNotIn(query, queries)

    def test_outdated_queries_works_with_specific_time_schedule(self):
        half_an_hour_ago = utcnow() - datetime.timedelta(minutes=30)
        query = self.factory.create_query(schedule=half_an_hour_ago.strftime('%H:%M'))
        query_result = self.factory.create_query_result(query=query.query_text, retrieved_at=half_an_hour_ago - datetime.timedelta(days=1))
        query.latest_query_data = query_result

        queries = models.Query.outdated_queries()
        self.assertIn(query, queries)

    def test_enqueues_query_only_once(self):
        """
        Only one query per data source with the same text will be reported by
        Query.outdated_queries().
        """
        query = self.factory.create_query(schedule="60")
        query2 = self.factory.create_query(
            schedule="60", query_text=query.query_text,
            query_hash=query.query_hash)
        retrieved_at = utcnow() - datetime.timedelta(minutes=10)
        query_result = self.factory.create_query_result(
            retrieved_at=retrieved_at, query_text=query.query_text,
            query_hash=query.query_hash)
        query.latest_query_data = query_result
        query2.latest_query_data = query_result

        self.assertEqual(list(models.Query.outdated_queries()), [query2])

    def test_enqueues_query_with_correct_data_source(self):
        """
        Queries from different data sources will be reported by
        Query.outdated_queries() even if they have the same query text.
        """
        query = self.factory.create_query(
            schedule="60", data_source=self.factory.create_data_source())
        query2 = self.factory.create_query(
            schedule="60", query_text=query.query_text,
            query_hash=query.query_hash)
        retrieved_at = utcnow() - datetime.timedelta(minutes=10)
        query_result = self.factory.create_query_result(
            retrieved_at=retrieved_at, query_text=query.query_text,
            query_hash=query.query_hash)
        query.latest_query_data = query_result
        query2.latest_query_data = query_result

        self.assertEqual(list(models.Query.outdated_queries()),
                         [query2, query])

    def test_enqueues_only_for_relevant_data_source(self):
        """
        If multiple queries with the same text exist, only ones that are
        scheduled to be refreshed are reported by Query.outdated_queries().
        """
        query = self.factory.create_query(schedule="60")
        query2 = self.factory.create_query(
            schedule="3600", query_text=query.query_text,
            query_hash=query.query_hash)
        retrieved_at = utcnow() - datetime.timedelta(minutes=10)
        query_result = self.factory.create_query_result(
            retrieved_at=retrieved_at, query_text=query.query_text,
            query_hash=query.query_hash)
        query.latest_query_data = query_result
        query2.latest_query_data = query_result

        self.assertEqual(list(models.Query.outdated_queries()), [query])

    def test_failure_extends_schedule(self):
        """
        Execution failures recorded for a query result in exponential backoff
        for scheduling future execution.
        """
        query = self.factory.create_query(schedule="60")
        query.schedule_failures = 4
        retrieved_at = utcnow() - datetime.timedelta(minutes=16)
        query_result = self.factory.create_query_result(
            retrieved_at=retrieved_at, query_text=query.query_text,
            query_hash=query.query_hash)
        query.latest_query_data = query_result

        self.assertEqual(list(models.Query.outdated_queries()), [])

        query_result.retrieved_at = utcnow() - datetime.timedelta(minutes=17)
        self.assertEqual(list(models.Query.outdated_queries()), [query])


class QueryArchiveTest(BaseTestCase):
    def setUp(self):
        super(QueryArchiveTest, self).setUp()

    def test_archive_query_sets_flag(self):
        query = self.factory.create_query()
        db.session.flush()
        query.archive()

        self.assertEquals(query.is_archived, True)

    def test_archived_query_doesnt_return_in_all(self):
        query = self.factory.create_query(schedule="1")
        yesterday = utcnow() - datetime.timedelta(days=1)
        query_result, _ = models.QueryResult.store_result(
            query.org_id, query.data_source, query.query_hash, query.query_text,
            "1", 123, yesterday)

        query.latest_query_data = query_result
        groups = list(models.Group.query.filter(models.Group.id.in_(query.groups)))
        self.assertIn(query, list(models.Query.all_queries([g.id for g in groups])))
        self.assertIn(query, models.Query.outdated_queries())
        db.session.flush()
        query.archive()

        self.assertNotIn(query, list(models.Query.all_queries([g.id for g in groups])))
        self.assertNotIn(query, models.Query.outdated_queries())

    def test_removes_associated_widgets_from_dashboards(self):
        widget = self.factory.create_widget()
        query = widget.visualization.query_rel
        db.session.commit()
        query.archive()
        db.session.flush()
        self.assertEqual(db.session.query(models.Widget).get(widget.id), None)

    def test_removes_scheduling(self):
        query = self.factory.create_query(schedule="1")

        query.archive()

        self.assertEqual(None, query.schedule)

    def test_deletes_alerts(self):
        subscription = self.factory.create_alert_subscription()
        query = subscription.alert.query_rel
        db.session.commit()
        query.archive()
        db.session.flush()
        self.assertEqual(db.session.query(models.Alert).get(subscription.alert.id), None)
        self.assertEqual(db.session.query(models.AlertSubscription).get(subscription.id), None)


class TestUnusedQueryResults(BaseTestCase):
    def test_returns_only_unused_query_results(self):
        two_weeks_ago = utcnow() - datetime.timedelta(days=14)
        qr = self.factory.create_query_result()
        query = self.factory.create_query(latest_query_data=qr)
        db.session.flush()
        unused_qr = self.factory.create_query_result(retrieved_at=two_weeks_ago)
        self.assertIn((unused_qr.id,), models.QueryResult.unused())
        self.assertNotIn((qr.id,), list(models.QueryResult.unused()))

    def test_returns_only_over_a_week_old_results(self):
        two_weeks_ago = utcnow() - datetime.timedelta(days=14)
        unused_qr = self.factory.create_query_result(retrieved_at=two_weeks_ago)
        db.session.flush()
        new_unused_qr = self.factory.create_query_result()

        self.assertIn((unused_qr.id,), models.QueryResult.unused())
        self.assertNotIn((new_unused_qr.id,), models.QueryResult.unused())


class TestQueryAll(BaseTestCase):
    def test_returns_only_queries_in_given_groups(self):
        ds1 = self.factory.create_data_source()
        ds2 = self.factory.create_data_source()

        group1 = models.Group(name="g1", org=ds1.org, permissions=['create', 'view'])
        group2 = models.Group(name="g2", org=ds1.org, permissions=['create', 'view'])

        q1 = self.factory.create_query(data_source=ds1)
        q2 = self.factory.create_query(data_source=ds2)

        db.session.add_all([
            ds1, ds2,
            group1, group2,
            q1, q2,
            models.DataSourceGroup(
                group=group1, data_source=ds1),
            models.DataSourceGroup(group=group2, data_source=ds2)
            ])
        db.session.flush()
        self.assertIn(q1, list(models.Query.all_queries([group1.id])))
        self.assertNotIn(q2, list(models.Query.all_queries([group1.id])))
        self.assertIn(q1, list(models.Query.all_queries([group1.id, group2.id])))
        self.assertIn(q2, list(models.Query.all_queries([group1.id, group2.id])))

    def test_skips_drafts(self):
        q = self.factory.create_query(is_draft=True)
        self.assertNotIn(q, models.Query.all_queries([self.factory.default_group.id]))

    def test_includes_drafts_of_given_user(self):
        q = self.factory.create_query(is_draft=True)
        self.assertIn(q, models.Query.all_queries([self.factory.default_group.id], user_id=q.user_id))

    def test_order_by_relationship(self):
        u1 = self.factory.create_user(name='alice')
        u2 = self.factory.create_user(name='bob')
        self.factory.create_query(user=u1)
        self.factory.create_query(user=u2)
        db.session.commit()
        # have to reset the order here with None since all_queries orders by
        # created_at by default
        base = models.Query.all_queries([self.factory.default_group.id]).order_by(None)
        qs1 = base.order_by(models.User.name)
        self.assertEqual(['alice', 'bob'], [q.user.name for q in qs1])
        qs2 = base.order_by(models.User.name.desc())
        self.assertEqual(['bob', 'alice'], [q.user.name for q in qs2])


class TestGroup(BaseTestCase):
    def test_returns_groups_with_specified_names(self):
        org1 = self.factory.create_org()
        org2 = self.factory.create_org()

        matching_group1 = models.Group(id=999, name="g1", org=org1)
        matching_group2 = models.Group(id=888, name="g2", org=org1)
        non_matching_group = models.Group(id=777, name="g1", org=org2)

        groups = models.Group.find_by_name(org1, ["g1", "g2"])
        self.assertIn(matching_group1, groups)
        self.assertIn(matching_group2, groups)
        self.assertNotIn(non_matching_group, groups)

    def test_returns_no_groups(self):
        org1 = self.factory.create_org()

        models.Group(id=999, name="g1", org=org1)
        self.assertEqual([], models.Group.find_by_name(org1, ["non-existing"]))


class TestQueryResultStoreResult(BaseTestCase):
    def setUp(self):
        super(TestQueryResultStoreResult, self).setUp()
        self.data_source = self.factory.data_source
        self.query = "SELECT 1"
        self.query_hash = gen_query_hash(self.query)
        self.runtime = 123
        self.utcnow = utcnow()
        self.data = "data"

    def test_stores_the_result(self):
        query_result, _ = models.QueryResult.store_result(
            self.data_source.org_id, self.data_source, self.query_hash,
            self.query, self.data, self.runtime, self.utcnow)

        self.assertEqual(query_result.data, self.data)
        self.assertEqual(query_result.runtime, self.runtime)
        self.assertEqual(query_result.retrieved_at, self.utcnow)
        self.assertEqual(query_result.query_text, self.query)
        self.assertEqual(query_result.query_hash, self.query_hash)
        self.assertEqual(query_result.data_source, self.data_source)

    def test_updates_existing_queries(self):
        query1 = self.factory.create_query(query_text=self.query)
        query2 = self.factory.create_query(query_text=self.query)
        query3 = self.factory.create_query(query_text=self.query)

        query_result, _ = models.QueryResult.store_result(
            self.data_source.org_id, self.data_source, self.query_hash,
            self.query, self.data, self.runtime, self.utcnow)

        self.assertEqual(query1.latest_query_data, query_result)
        self.assertEqual(query2.latest_query_data, query_result)
        self.assertEqual(query3.latest_query_data, query_result)

    def test_doesnt_update_queries_with_different_hash(self):
        query1 = self.factory.create_query(query_text=self.query)
        query2 = self.factory.create_query(query_text=self.query)
        query3 = self.factory.create_query(query_text=self.query + "123")

        query_result, _ = models.QueryResult.store_result(
            self.data_source.org_id, self.data_source, self.query_hash,
            self.query, self.data, self.runtime, self.utcnow)

        self.assertEqual(query1.latest_query_data, query_result)
        self.assertEqual(query2.latest_query_data, query_result)
        self.assertNotEqual(query3.latest_query_data, query_result)

    def test_doesnt_update_queries_with_different_data_source(self):
        query1 = self.factory.create_query(query_text=self.query)
        query2 = self.factory.create_query(query_text=self.query)
        query3 = self.factory.create_query(query_text=self.query, data_source=self.factory.create_data_source())

        query_result, _ = models.QueryResult.store_result(
            self.data_source.org_id, self.data_source, self.query_hash,
            self.query, self.data, self.runtime, self.utcnow)

        self.assertEqual(query1.latest_query_data, query_result)
        self.assertEqual(query2.latest_query_data, query_result)
        self.assertNotEqual(query3.latest_query_data, query_result)


class TestEvents(BaseTestCase):
    def raw_event(self):
        timestamp = 1411778709.791
        user = self.factory.user
        created_at = datetime.datetime.utcfromtimestamp(timestamp)
        db.session.flush()
        raw_event = {"action": "view",
                      "timestamp": timestamp,
                      "object_type": "dashboard",
                      "user_id": user.id,
                      "object_id": 1,
                      "org_id": 1}

        return raw_event, user, created_at

    def test_records_event(self):
        raw_event, user, created_at = self.raw_event()

        event = models.Event.record(raw_event)
        db.session.flush()
        self.assertEqual(event.user, user)
        self.assertEqual(event.action, "view")
        self.assertEqual(event.object_type, "dashboard")
        self.assertEqual(event.object_id, 1)
        self.assertEqual(event.created_at, created_at)

    def test_records_additional_properties(self):
        raw_event, _, _ = self.raw_event()
        additional_properties = {'test': 1, 'test2': 2, 'whatever': "abc"}
        raw_event.update(additional_properties)

        event = models.Event.record(raw_event)

        self.assertDictEqual(event.additional_properties, additional_properties)


def _set_up_dashboard_test(d):
    d.g1 = d.factory.create_group(name='First', permissions=['create', 'view'])
    d.g2 = d.factory.create_group(name='Second',  permissions=['create', 'view'])
    d.ds1 = d.factory.create_data_source()
    d.ds2 = d.factory.create_data_source()
    db.session.flush()
    d.u1 = d.factory.create_user(group_ids=[d.g1.id])
    d.u2 = d.factory.create_user(group_ids=[d.g2.id])
    db.session.add_all([
        models.DataSourceGroup(group=d.g1, data_source=d.ds1),
        models.DataSourceGroup(group=d.g2, data_source=d.ds2)
    ])
    d.q1 = d.factory.create_query(data_source=d.ds1)
    d.q2 = d.factory.create_query(data_source=d.ds2)
    d.v1 = d.factory.create_visualization(query_rel=d.q1)
    d.v2 = d.factory.create_visualization(query_rel=d.q2)
    d.w1 = d.factory.create_widget(visualization=d.v1)
    d.w2 = d.factory.create_widget(visualization=d.v2)
    d.w3 = d.factory.create_widget(visualization=d.v2, dashboard=d.w2.dashboard)
    d.w4 = d.factory.create_widget(visualization=d.v2)
    d.w5 = d.factory.create_widget(visualization=d.v1, dashboard=d.w4.dashboard)
    d.w1.dashboard.is_draft = False
    d.w2.dashboard.is_draft = False
    d.w4.dashboard.is_draft = False

class TestDashboardAll(BaseTestCase):
    def setUp(self):
        super(TestDashboardAll, self).setUp()
        _set_up_dashboard_test(self)

    def test_requires_group_or_user_id(self):
        d1 = self.factory.create_dashboard()
        self.assertNotIn(d1, list(models.Dashboard.all(
           d1.user.org, d1.user.group_ids, None)))
        l2 = list(models.Dashboard.all(
            d1.user.org, [0], d1.user.id))
        self.assertIn(d1, l2)

    def test_returns_dashboards_based_on_groups(self):
        self.assertIn(self.w1.dashboard, list(models.Dashboard.all(
            self.u1.org, self.u1.group_ids, None)))
        self.assertIn(self.w2.dashboard, list(models.Dashboard.all(
            self.u2.org, self.u2.group_ids, None)))
        self.assertNotIn(self.w1.dashboard, list(models.Dashboard.all(
            self.u2.org, self.u2.group_ids, None)))
        self.assertNotIn(self.w2.dashboard, list(models.Dashboard.all(
            self.u1.org, self.u1.group_ids, None)))

    def test_returns_each_dashboard_once(self):
        dashboards = list(models.Dashboard.all(self.u2.org, self.u2.group_ids, None))
        self.assertEqual(len(dashboards), 2)

    def test_returns_dashboard_you_have_partial_access_to(self):
        self.assertIn(self.w5.dashboard, models.Dashboard.all(self.u1.org, self.u1.group_ids, None))

    def test_returns_dashboards_created_by_user(self):
        d1 = self.factory.create_dashboard(user=self.u1)
        db.session.flush()
        self.assertIn(d1, list(models.Dashboard.all(self.u1.org, self.u1.group_ids, self.u1.id)))
        self.assertIn(d1, list(models.Dashboard.all(self.u1.org, [0], self.u1.id)))
        self.assertNotIn(d1, list(models.Dashboard.all(self.u2.org, self.u2.group_ids, self.u2.id)))

    def test_returns_dashboards_with_text_widgets(self):
        w1 = self.factory.create_widget(visualization=None)

        self.assertIn(w1.dashboard, models.Dashboard.all(self.u1.org, self.u1.group_ids, None))
        self.assertIn(w1.dashboard, models.Dashboard.all(self.u2.org, self.u2.group_ids, None))

    def test_returns_dashboards_from_current_org_only(self):
        w1 = self.factory.create_widget(visualization=None)

        user = self.factory.create_user(org=self.factory.create_org())

        self.assertIn(w1.dashboard, models.Dashboard.all(self.u1.org, self.u1.group_ids, None))
        self.assertNotIn(w1.dashboard, models.Dashboard.all(user.org, user.group_ids, None))
