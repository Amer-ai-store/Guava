SELECT s.name AS schema_name, o.name AS object_name, o.type AS object_type,
c.name AS column_name, t.name AS data_type, c.is_nullable, c.column_id
FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
JOIN sys.columns c ON c.object_id=o.object_id
JOIN sys.types t ON t.user_type_id=c.user_type_id
WHERE o.type IN ('U','V') AND o.is_ms_shipped=0
ORDER BY s.name,o.name,c.column_id