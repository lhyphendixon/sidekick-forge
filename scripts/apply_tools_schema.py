#!/usr/bin/env python3
import os
from supabase import create_client

PLATFORM_SQL = os.path.join(os.path.dirname(__file__), '..', 'migrations', 'add_tools_platform.sql')
TENANT_SQL = os.path.join(os.path.dirname(__file__), '..', 'migrations', 'add_tools_tenant.sql')

def read_sql(path: str) -> str:
    with open(path, 'r') as f:
        return f.read()

def main():
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_service_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    tenant_url = os.getenv('TENANT_SUPABASE_URL')
    tenant_service_key = os.getenv('TENANT_SERVICE_ROLE_KEY')

    if not supabase_url or not supabase_service_key:
        print('Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to apply platform schema.')
    else:
        print('--- Platform schema (tools, agent_tools) ---')
        print(read_sql(PLATFORM_SQL))

    if tenant_url and tenant_service_key:
        print('\n--- Tenant schema (tools) ---')
        print(read_sql(TENANT_SQL))
        print('\nExecute the above tenant SQL in the target tenant project(s).')
    else:
        print('\nTo print tenant schema, set TENANT_SUPABASE_URL and TENANT_SERVICE_ROLE_KEY.')

if __name__ == '__main__':
    main()


