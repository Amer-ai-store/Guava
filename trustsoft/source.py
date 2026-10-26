"""Bounded, serialized SQL reads. No client-provided SQL or export operations."""
import datetime
import decimal
import json
import os
import threading

SECTIONS = ('sales', 'purchases', 'accounting', 'checks', 'inventory', 'users', 'operations', 'financial', 'vouchers', 'payments', 'audit')
CATALOG = """SELECT s.name AS schema_name, o.name AS object_name, o.type AS object_type,
c.name AS column_name, t.name AS data_type, c.is_nullable, c.column_id
FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
JOIN sys.columns c ON c.object_id=o.object_id
JOIN sys.types t ON t.user_type_id=c.user_type_id
WHERE o.type IN ('U','V') AND o.is_ms_shipped=0
ORDER BY s.name,o.name,c.column_id"""
RELATIONS = """SELECT fk.name AS relation_name, OBJECT_SCHEMA_NAME(f.parent_object_id) AS parent_schema,
OBJECT_NAME(f.parent_object_id) AS parent_table, COL_NAME(f.parent_object_id,f.parent_column_id) AS parent_column,
OBJECT_SCHEMA_NAME(f.referenced_object_id) AS referenced_schema,
OBJECT_NAME(f.referenced_object_id) AS referenced_table,
COL_NAME(f.referenced_object_id,f.referenced_column_id) AS referenced_column
FROM sys.foreign_key_columns f JOIN sys.foreign_keys fk ON fk.object_id=f.constraint_object_id"""
IDENTITY = 'SELECT @@SERVERNAME AS server_name, DB_NAME() AS database_name, ORIGINAL_LOGIN() AS login_name, SYSUTCDATETIME() AS server_utc'
PERMISSIONS = """SELECT permission_name FROM sys.fn_my_permissions(NULL, 'DATABASE')
WHERE permission_name IN ('INSERT','UPDATE','DELETE','EXECUTE','ALTER','CONTROL','TAKE OWNERSHIP','CREATE TABLE','CREATE VIEW','CREATE PROCEDURE')
UNION ALL SELECT 'SERVER_PRIVILEGE' WHERE IS_SRVROLEMEMBER('sysadmin')=1
UNION ALL SELECT 'OBJECT_WRITE' FROM sys.objects o JOIN sys.schemas s ON o.schema_id=s.schema_id
WHERE o.is_ms_shipped=0 AND (HAS_PERMS_BY_NAME(QUOTENAME(s.name)+'.'+QUOTENAME(o.name),'OBJECT','INSERT')=1
OR HAS_PERMS_BY_NAME(QUOTENAME(s.name)+'.'+QUOTENAME(o.name),'OBJECT','UPDATE')=1
OR HAS_PERMS_BY_NAME(QUOTENAME(s.name)+'.'+QUOTENAME(o.name),'OBJECT','DELETE')=1
OR HAS_PERMS_BY_NAME(QUOTENAME(s.name)+'.'+QUOTENAME(o.name),'OBJECT','CONTROL')=1
OR HAS_PERMS_BY_NAME(QUOTENAME(s.name)+'.'+QUOTENAME(o.name),'OBJECT','ALTER')=1
OR HAS_PERMS_BY_NAME(QUOTENAME(s.name)+'.'+QUOTENAME(o.name),'OBJECT','EXECUTE')=1)"""

class SourceError(Exception):
    pass

def encode(value):
    if isinstance(value, (datetime.datetime, datetime.date, decimal.Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return '[binary omitted]'
    raise TypeError(type(value).__name__)

def quote(name):
    return '[' + name.replace(']', ']]') + ']'

class SqlSource:
    def __init__(self):
        self.lock = threading.Lock()
        path = os.getenv('TRUSTSOFT_MAPPING')
        self.mapping = json.load(open(path, encoding='utf-8')) if path else {}
        if set(self.mapping) - set(SECTIONS):
            raise ValueError('Unknown mapping section')

    def snapshot(self):
        connection_string = os.getenv('TRUSTSOFT_SQL_CONNECTION')
        if not connection_string:
            raise SourceError('sql_not_configured')
        try:
            import pyodbc
        except ImportError:
            raise SourceError('sql_driver_missing') from None
        # Require certificate verification. This is in addition to server-side reader permissions.
        parts = dict(p.strip().lower().split('=', 1) for p in connection_string.split(';') if '=' in p)
        if parts.get('encrypt') not in ('yes', 'mandatory', 'strict') or parts.get('trustservercertificate', 'no') not in ('no', 'false'):
            raise SourceError('tls_configuration_required')
        with self.lock:
            try:
                conn = pyodbc.connect(connection_string, timeout=5, autocommit=False)
                try:
                    conn.timeout = 8
                    def read(sql, limit=20000):
                        cursor = conn.cursor()
                        try:
                            cursor.execute(sql)
                            columns = [c[0] for c in cursor.description]
                            rows = cursor.fetchmany(limit + 1)
                            if len(rows) > limit:
                                raise SourceError('metadata_limit_exceeded')
                            return [dict(zip(columns, r)) for r in rows]
                        finally:
                            cursor.close()
                    read('SET LOCK_TIMEOUT 2000; SET DEADLOCK_PRIORITY LOW; SET TRANSACTION ISOLATION LEVEL READ COMMITTED; SELECT 1 AS ok')
                    if read(PERMISSIONS):
                        raise SourceError('read_only_principal_required')
                    identity = read(IDENTITY)[0]
                    if identity['database_name'] != 'Jawafa_DB':
                        raise SourceError('wrong_database')
                    server = str(identity['server_name']).split('\\')[0].split('.')[0].upper()
                    if server != 'JAWAFFASVR':
                        raise SourceError('wrong_server')
                    catalog = read(CATALOG)
                    relations = read(RELATIONS)
                    objects = {}
                    for c in catalog:
                        objects.setdefault((c['schema_name'], c['object_name']), set()).add(c['column_name'])
                    sections = {}
                    for name in SECTIONS:
                        cfg = self.mapping.get(name)
                        if not cfg:
                            sections[name] = {'state': 'unmapped', 'rows': []}
                            continue
                        key = (cfg['schema'], cfg['object'])
                        columns = cfg['columns']  # Canonical alias -> actual column
                        order = cfg.get('order_by', [])
                        if not columns or len(columns) > 30 or not order or not set(columns.values()).union(order) <= objects.get(key, set()):
                            raise SourceError('invalid_mapping')
                        projection = ','.join(quote(v)+' AS '+quote(k) for k,v in columns.items())
                        sql = 'SELECT TOP (200) '+projection+' FROM '+'.'.join(map(quote,key))+' ORDER BY '+','.join(map(quote,order))
                        sections[name] = {'state': 'sample', 'rows': read(sql, 200), 'limit': 200, 'scope': cfg.get('scope', 'Unverified business scope')}
                    return {'identity': identity, 'catalog': catalog, 'relations': relations, 'sections': sections}
                finally:
                    conn.rollback()
                    conn.close()
            except SourceError:
                raise
            except Exception:
                # Do not disclose driver errors containing connection strings or customer data.
                raise SourceError('sql_connection_or_query_failed') from None
