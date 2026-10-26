import os
import sys
import types
import unittest
from unittest.mock import patch
from trustsoft.source import SqlSource, SourceError

class Cursor:
    description = []
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql):
        self.connection.queries.append(sql)
        if 'fn_my_permissions' in sql:
            self.rows = [{'permission_name':'UPDATE'}] if self.connection.writer else []
        elif '@@SERVERNAME' in sql:
            self.rows = [{'server_name':'JAWAFFASVR','database_name':'Jawafa_DB'}]
        elif 'FROM sys.objects o JOIN' in sql:
            self.rows = [{'schema_name':'dbo','object_name':'ApprovedSales','column_name':'Id'},
                         {'schema_name':'dbo','object_name':'ApprovedSales','column_name':'Amount'}]
        elif 'foreign_key_columns' in sql:
            self.rows = []
        elif 'TOP (200)' in sql:
            self.rows = [{'id':1,'amount':'20'}]
        else:
            self.rows = [{'ok':1}]
        self.description = [(k,) for k in self.rows[0]] if self.rows else [('unused',)]
    def fetchmany(self, count):
        return [tuple(r.values()) for r in self.rows[:count]]
    def close(self):
        pass

class Connection:
    def __init__(self, writer=False):
        self.writer=writer
        self.queries=[]
        self.rolled_back=False
        self.closed=False
    def cursor(self):
        return Cursor(self)
    def rollback(self):
        self.rolled_back=True
    def close(self):
        self.closed=True

class SqlTests(unittest.TestCase):
    def run_source(self, writer=False, mapping=None, tls='yes'):
        self.connection=Connection(writer)
        fake=types.SimpleNamespace(connect=lambda *a,**k:self.connection)
        with patch.dict(sys.modules, {'pyodbc':fake}), patch.dict(os.environ, {'TRUSTSOFT_SQL_CONNECTION':'Encrypt='+tls+';TrustServerCertificate=no;','TRUSTSOFT_MAPPING':''}):
            source=SqlSource()
            source.mapping=mapping or {}
            return source.snapshot()

    def test_reader_catalog_and_bounded_query(self):
        result=self.run_source(mapping={'sales':{'schema':'dbo','object':'ApprovedSales','columns':{'id':'Id','amount':'Amount'},'order_by':['Id']}})
        self.assertEqual(result['sections']['sales']['rows'],[{'id':1,'amount':'20'}])
        self.assertTrue(any('TOP (200)' in q for q in self.connection.queries))
        self.assertTrue(self.connection.closed and self.connection.rolled_back)

    def test_writer_rejected_before_business_read(self):
        with self.assertRaisesRegex(SourceError,'read_only_principal_required'):
            self.run_source(writer=True)
        self.assertFalse(any('TOP (200)' in q for q in self.connection.queries))
        self.assertTrue(self.connection.closed and self.connection.rolled_back)

    def test_mapping_injection_rejected(self):
        with self.assertRaisesRegex(SourceError,'invalid_mapping'):
            self.run_source(mapping={'sales':{'schema':'dbo','object':'ApprovedSales;DROP','columns':{'id':'Id'},'order_by':['Id']}})

    def test_tls_required(self):
        with self.assertRaisesRegex(SourceError,'tls_configuration_required'):
            self.run_source(tls='no')
