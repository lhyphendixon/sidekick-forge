#!/usr/bin/env python3
import json
from datetime import datetime

# Load the schema info
with open('/root/sidekick-forge/backups/autonomite_schema_20250810_133908.json', 'r') as f:
    schema_data = json.load(f)

# Generate SQL DDL statements
sql_statements = []
sql_statements.append('-- Autonomite Database Schema Export')
sql_statements.append(f"-- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
sql_statements.append('-- Source: https://yuowazxcxwhczywurmmw.supabase.co')
sql_statements.append('')

# Type mapping for SQL
type_map = {
    'uuid': 'UUID',
    'text': 'TEXT',
    'integer': 'INTEGER',
    'numeric': 'NUMERIC',
    'boolean': 'BOOLEAN',
    'timestamp': 'TIMESTAMP WITH TIME ZONE',
    'jsonb': 'JSONB',
    'array': 'TEXT[]',
    'unknown': 'TEXT'
}

for table_name, table_info in schema_data['schema'].items():
    if table_info.get('exists'):
        sql_statements.append(f"-- Table: {table_name}")
        sql_statements.append(f"CREATE TABLE IF NOT EXISTS {table_name} (")
        
        columns = table_info.get('columns', {})
        if columns:
            col_defs = []
            for col_name, col_type in columns.items():
                sql_type = type_map.get(col_type, 'TEXT')
                
                # Add constraints for common columns
                constraint = ''
                if col_name == 'id':
                    constraint = ' PRIMARY KEY'
                elif col_name in ['created_at', 'updated_at']:
                    constraint = ' DEFAULT NOW()'
                elif col_name.endswith('_id') and col_name != 'id':
                    # Foreign key reference (we'll note it but not enforce in this export)
                    ref_table = col_name.replace('_id', '')
                    constraint = f" -- FK to {ref_table}"
                
                col_defs.append(f"    {col_name} {sql_type}{constraint}")
            
            sql_statements.append(',\n'.join(col_defs))
        elif table_info.get('empty'):
            sql_statements.append('    -- Table structure unknown (empty table)')
        
        sql_statements.append(');')
        sql_statements.append('')

# Save SQL schema
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
sql_file = f'/root/sidekick-forge/backups/autonomite_schema_{timestamp}.sql'
with open(sql_file, 'w') as f:
    f.write('\n'.join(sql_statements))

print('SQL Schema Export:')
print('=' * 60)
print('\n'.join(sql_statements[:50]))  # First 50 lines
if len(sql_statements) > 50:
    print('\n... (continued in file)')
print('=' * 60)
print(f'Full SQL schema saved to: {sql_file}')

# Also create a markdown documentation
doc_lines = []
doc_lines.append('# Autonomite Database Schema Documentation')
doc_lines.append(f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
doc_lines.append('\n## Tables\n')

for table_name, table_info in sorted(schema_data['schema'].items()):
    if table_info.get('exists'):
        doc_lines.append(f"### {table_name}")
        columns = table_info.get('columns', {})
        if columns:
            doc_lines.append('\n| Column | Type |')
            doc_lines.append('|--------|------|')
            for col_name, col_type in sorted(columns.items()):
                doc_lines.append(f"| {col_name} | {col_type} |")
        else:
            doc_lines.append('\n*Empty table - structure unknown*')
        doc_lines.append('')

doc_file = f'/root/sidekick-forge/backups/autonomite_schema_{datetime.now().strftime("%Y%m%d")}.md'
with open(doc_file, 'w') as f:
    f.write('\n'.join(doc_lines))

print(f'\nMarkdown documentation saved to: {doc_file}')