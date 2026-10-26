SELECT fk.name AS relation_name, OBJECT_SCHEMA_NAME(f.parent_object_id) AS parent_schema,
OBJECT_NAME(f.parent_object_id) AS parent_table, COL_NAME(f.parent_object_id,f.parent_column_id) AS parent_column,
OBJECT_SCHEMA_NAME(f.referenced_object_id) AS referenced_schema,
OBJECT_NAME(f.referenced_object_id) AS referenced_table,
COL_NAME(f.referenced_object_id,f.referenced_column_id) AS referenced_column
FROM sys.foreign_key_columns f JOIN sys.foreign_keys fk ON fk.object_id=f.constraint_object_id