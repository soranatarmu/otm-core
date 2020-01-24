# -*- coding: utf-8 -*-


import csv
import json

from treemap.udf import UserDefinedFieldDefinition
from treemap.lib.dates import DATETIME_FORMAT
from treemap.tests.test_views import LocalMediaTestCase, media_dir
from treemap.tests import (make_instance, make_commander_user, make_request,
                           set_write_permissions, make_commander_role,
                           make_admin_user)
from treemap.tests.base import OTMTestCase
from treemap.models import Species, Plot, Tree, User, InstanceUser
from treemap.audit import Audit, add_default_permissions

from exporter.models import ExportJob
from exporter import tasks
from exporter.lib import export_enabled_for
from exporter.views import begin_export, check_export, users_json, users_csv

from django.test.utils import override_settings
from django.utils.timezone import now
from django.core.exceptions import ValidationError
from django.contrib.auth.models import AnonymousUser
import datetime


class AsyncCSVTestCase(LocalMediaTestCase):

    def setUp(self):
        super(AsyncCSVTestCase, self).setUp()
        self.instance = make_instance()
        self.user = make_commander_user(self.instance)

        self.unprivileged_user = User.objects.create_user(username='foo',
                                                          email='foo@bar.com',
                                                          password='bar')

    def assertCSVRowValue(self, csv_file, row_index, headers_and_values):
        # decode bytes object into string list required by the csv reader
        str_rows = [line.decode('utf-8') for line in csv_file]

        # strip the BOM out
        str_rows[0] = str_rows[0][1:]

        csvreader = csv.reader(str_rows, delimiter=",")
        reader_rows = list(csvreader)

        self.assertTrue(len(reader_rows) > 1)
        for (header, value) in headers_and_values.items():
            target_column = reader_rows[0].index(header)
            self.assertEqual(value, reader_rows[row_index][target_column])

    def assertTaskProducesCSV(self, user, model, assert_fields_and_values):
        self._assertTaskProducesCSVBase(user, model, assert_fields_and_values)

        # run the test again without a user
        # catches original version of:
        # https://github.com/OpenTreeMap/otm-core/issues/1384
        # "initial_qs referenced before assignment"
        add_default_permissions(self.instance,
                                [self.instance.default_role])
        self._assertTaskProducesCSVBase(None, model, assert_fields_and_values)

    def _assertTaskProducesCSVBase(self, user, model,
                                   assert_fields_and_values):
        job = ExportJob(instance=self.instance, user=user)
        job.save()
        tasks.async_csv_export(job.pk, model, '', '')

        # Refresh model with outfile
        job = ExportJob.objects.get(pk=job.pk)
        self.assertCSVRowValue(job.outfile, 1, assert_fields_and_values)

    def assertPsuedoAsyncTaskWorks(self, model,
                                   user,
                                   assertion_field, assertion_value,
                                   assertion_filename):

        request = make_request(user=user)
        ctx = begin_export(request, self.instance, model)
        self.assertIn('job_id', list(ctx.keys()))
        self.assertEqual(ctx['start_status'], 'OK')

        job_id = ctx['job_id']
        job = ExportJob.objects.get(pk=job_id)

        self.assertCSVRowValue(job.outfile, 1,
                               {assertion_field: assertion_value})

        ctx = check_export(request, self.instance, job_id)
        self.assertIn('.csv', ctx['url'])
        self.assertEqual(ctx['status'], 'COMPLETE')

        self.assertRegex(job.outfile.name, assertion_filename)


class ExportTreeTaskTest(AsyncCSVTestCase):

    def setUp(self):
        super(ExportTreeTaskTest, self).setUp()

        set_write_permissions(self.instance, self.user,
                              'Plot', ['udf:Test choice'])
        set_write_permissions(self.instance, self.user,
                              'Tree', ['udf:Test int'])

        UserDefinedFieldDefinition.objects.create(
            instance=self.instance,
            model_type='Plot',
            datatype=json.dumps({'type': 'choice',
                                 'choices': ['a', 'b', 'c']}),
            iscollection=False,
            name='Test choice')

        UserDefinedFieldDefinition.objects.create(
            instance=self.instance,
            model_type='Tree',
            datatype=json.dumps({'type': 'int'}),
            iscollection=False,
            name='Test int')

        p = Plot(geom=self.instance.center, instance=self.instance,
                 address_street="123 Main Street")
        p.udfs['Test choice'] = 'a'

        p.save_with_user(self.user)

        t = Tree(plot=p, instance=self.instance, diameter=2)
        t.udfs['Test int'] = 4

        t.save_with_user(self.user)

    @media_dir
    def test_tree_task_unit(self):
        self.assertTaskProducesCSV(
            self.user, 'tree', {'Diameter': '2.0',
                                'Tree: Test int': '4',
                                'Planting Site: Test choice': 'a'})

    @media_dir
    @override_settings(FEATURE_BACKEND_FUNCTION=None)
    def test_psuedo_async_tree_export(self):
        self.assertPsuedoAsyncTaskWorks('tree', self.user, 'Diameter', '2.0',
                                        '.*tree_export(_\d+)?\.csv')


class ExportSpeciesTaskTest(AsyncCSVTestCase):

    def setUp(self):
        super(ExportSpeciesTaskTest, self).setUp()

        species = Species(common_name='foo', instance=self.instance)
        species.save_with_user(self.user)

    @media_dir
    def test_species_task_unit(self):
        self.assertTaskProducesCSV(
            self.user, 'species', {'Common Name': 'foo'})

    @media_dir
    @override_settings(FEATURE_BACKEND_FUNCTION=None)
    def test_psuedo_async_species_export(self):
        self.assertPsuedoAsyncTaskWorks('species', self.user, 'Common Name',
                                        'foo', '.*species_export(_\d+)?\.csv')


class UserExportsTestCase(OTMTestCase):

    def assertUserJSON(self, data, expectations):
        for key, expectation in list(expectations.items()):
            value = data[key]
            self.assertEqual(expectation, value,
                             "failure for key '%s': expected '%s', found '%s'"
                             % (key, expectation, value))

    def setUp(self):
        self.instance = make_instance()
        self.commander = make_commander_user(self.instance, "comm")

        # Note unicode '⅀' is on purpose
        self.user1 = User(username='estraven', password='estraven',
                          email='estraven@example.com',
                          organization='karhide',
                          first_name='therem', last_name='⅀straven')

        self.user1.save_with_user(self.commander)

        self.user2 = User(username='genly', password='genly',
                          email='genly@example.com',
                          first_name='genly', last_name='ai',
                          allow_email_contact=True)
        self.user2.save_with_user(self.commander)

        self.user3 = User(username='argaven_xv', password='argaven_xv',
                          email='argaven_xv@example.com')
        self.user3.save_with_user(self.commander)

        role = make_commander_role(self.instance)
        iuser1 = InstanceUser(instance=self.instance, user=self.user1,
                              role=role)
        iuser1.save_with_user(self.user1)
        iuser2 = InstanceUser(instance=self.instance, user=self.user2,
                              role=role)
        iuser2.save_with_user(self.user2)

        self.plot = Plot(geom=self.instance.center, readonly=False,
                         instance=self.instance, width=4)
        self.plot.save_with_user(self.user1)

        self.tree = Tree(instance=self.instance, plot=self.plot, diameter=3)
        self.tree.save_with_user(self.user2)


class UserExportsTest(UserExportsTestCase):

    def get_csv_data_with_base_assertions(self):
        resp = users_csv(make_request(), self.instance)

        # decode bytes object into string list required by the csv reader
        str_rows = [line.decode('utf-8') for line in resp]

        # strip the BOM and entry line out
        reader = csv.reader(str_rows[2:])

        # grab and strip the header
        header = next(reader)

        reader_rows = list(reader)

        data = (lambda h=header, rows=reader_rows:
                [dict(list(zip(h, [x for x in row]))) for row in rows])()

        commander, user1data, user2data = data
        self.assertEqual(commander['username'], self.commander.username)
        self.assertUserJSON(user1data,
                            {'username': self.user1.username,
                             'email': '',
                             'email_hash': self.user1.email_hash,
                             'allow_email_contact': 'False',
                             'role': 'commander',
                             'created': str(self.user1.created),
                             'last_edit_model': 'Plot',
                             'last_edit_model_id': str(self.plot.pk),
                             'last_edit_instance_id': str(self.instance.pk),
                             'last_edit_user_id': str(self.user1.pk)})

        self.assertUserJSON(user2data,
                            {'email': 'genly@example.com',
                             'email_hash': self.user2.email_hash,
                             'last_edit_model': 'Tree',
                             'last_edit_model_id': str(self.tree.pk),
                             'last_edit_instance_id': str(self.instance.pk),
                             'last_edit_user_id': str(self.user2.pk)})
        return data

    def test_export_users_csv_keep_info_private(self):
        data = self.get_csv_data_with_base_assertions()
        commander, user1data, user2data = data
        self.assertEqual(commander['username'], self.commander.username)
        self.assertUserJSON(user1data,
                            {'first_name': '',
                             'last_name': '',
                             'organization': ''})

    def test_export_users_csv_make_info_public(self):
        self.user1.make_info_public = True
        self.user1.save()
        data = self.get_csv_data_with_base_assertions()
        commander, user1data, user2data = data
        self.assertEqual(commander['username'], self.commander.username)
        self.assertUserJSON(user1data,
                            {'first_name': self.user1.first_name,
                             'last_name': self.user1.last_name,
                             'organization': self.user1.organization})

    def test_export_users_json_keep_info_private(self):
        resp = users_json(make_request(), self.instance)

        data = json.loads(resp.content)

        commander, user1data, user2data = data
        self.assertFalse('first_name' in user1data)

    def test_export_users_json_make_info_public(self):
        self.user1.make_info_public = True
        self.user1.save()

        resp = users_json(make_request(), self.instance)

        data = json.loads(resp.content)

        commander, user1data, user2data = data

        self.assertEqual(commander['username'], self.commander.username)
        self.assertEqual(user1data.get('email'), None)
        self.assertUserJSON(user1data,
                            {'username': self.user1.username,
                             'email_hash': self.user1.email_hash,
                             'first_name': self.user1.first_name,
                             'last_name': self.user1.last_name,
                             'organization': self.user1.organization,
                             'allow_email_contact': 'False',
                             'role': 'commander',
                             'created': str(self.user1.created)})

        self.assertUserJSON(user2data,
                            {'last_edit_model': 'Tree',
                             'last_edit_model_id': str(self.tree.pk),
                             'last_edit_instance_id': str(self.instance.pk),
                             'last_edit_user_id': str(self.user2.pk),
                             'email': 'genly@example.com',
                             'email_hash': self.user2.email_hash})

    def test_min_edit_date(self):
        last_week = now() - datetime.timedelta(days=7)
        two_days_ago = now() - datetime.timedelta(days=2)
        yesterday = now() - datetime.timedelta(days=1)
        tda_ts = two_days_ago.strftime(DATETIME_FORMAT)

        Audit.objects.filter(user=self.user1)\
            .update(created=last_week, updated=last_week)

        Audit.objects.filter(user=self.commander)\
            .update(created=last_week, updated=last_week)

        Audit.objects.filter(user=self.user2)\
            .update(created=yesterday, updated=yesterday)

        resp = users_json(make_request({'minEditDate': tda_ts}), self.instance)

        data = json.loads(resp.content)

        self.assertEqual(len(data), 1)

        self.assertEqual(data[0]['username'], self.user2.username)

    def test_min_join_date(self):
        last_week = now() - datetime.timedelta(days=7)
        two_days_ago = now() - datetime.timedelta(days=2)
        yesterday = now() - datetime.timedelta(days=1)
        tda_ts = two_days_ago.strftime(DATETIME_FORMAT)

        Audit.objects.filter(model='InstanceUser')\
            .filter(model_id=self.user1.get_instance_user(self.instance).pk)\
            .update(created=last_week)

        Audit.objects.filter(model='InstanceUser')\
            .filter(model_id=
                    self.commander.get_instance_user(self.instance).pk)\
            .update(created=last_week)

        Audit.objects.filter(model='InstanceUser')\
            .filter(model_id=self.user2.get_instance_user(self.instance).pk)\
            .update(created=yesterday)

        resp = users_json(make_request({'minJoinDate': tda_ts}), self.instance)

        data = json.loads(resp.content)

        self.assertEqual(len(data), 1)

        self.assertEqual(data[0]['username'], self.user2.username)

    def test_min_join_date_validation(self):
        with self.assertRaises(ValidationError):
            users_json(make_request({"minJoinDate": "fsdafsa"}), self.instance)

    def test_min_edit_date_validation(self):
        with self.assertRaises(ValidationError):
            users_json(make_request({"minEditDate": "fsdafsa"}), self.instance)


class ExportEnabledTestCase(OTMTestCase):

    _export_enabled = 'treemap.plugin.always_true'
    _export_disabled = 'treemap.plugin.always_false'

    def setUp(self):
        super(ExportEnabledTestCase, self).setUp()
        self.instance = make_instance()
        self.admin = make_admin_user(self.instance)
        # "commander" is an instance user, but not an admin
        self.non_admin = make_commander_user(self.instance)

    def assert_admin_can_export(self, b):
        self.assertEqual(b, export_enabled_for(self.instance, self.admin))

    def assert_user_can_export(self, b):
        self.assertEqual(b, export_enabled_for(self.instance, self.non_admin))

    def assert_anonymous_can_export(self, b):
        self.assertEqual(b, export_enabled_for(self.instance, AnonymousUser()))

    @override_settings(FEATURE_BACKEND_FUNCTION=_export_disabled)
    def test_export_disabled_for_all_when_feature_disabled(self):
        self.assert_user_can_export(False)
        self.assert_admin_can_export(False)
        self.assert_anonymous_can_export(False)

    @override_settings(FEATURE_BACKEND_FUNCTION=_export_enabled)
    def test_export_enabled_for_all_by_default(self):
        self.assert_user_can_export(True)
        self.assert_admin_can_export(True)
        self.assert_anonymous_can_export(True)

    @override_settings(FEATURE_BACKEND_FUNCTION=_export_enabled)
    def test_export_disabled_for_non_admins(self):
        self.instance.non_admins_can_export = False
        self.instance.save()
        self.assert_user_can_export(False)
        self.assert_admin_can_export(True)
        self.assert_anonymous_can_export(False)
