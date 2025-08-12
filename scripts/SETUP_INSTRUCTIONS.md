# Sidekick Forge Platform Setup Instructions

## Manual Database Setup Required

Since Supabase doesn't allow direct SQL execution via API, you need to manually execute these SQL scripts in the Supabase dashboard:

### Step 1: Create the clients table

1. Go to https://supabase.com/dashboard
2. Select your **Sidekick Forge** project
3. Navigate to **SQL Editor**
4. Paste and execute the contents of `create_clients_table.sql`
5. Verify the table was created in the Table Editor

### Step 2: Insert Autonomite as the first client

1. Still in the SQL Editor
2. (Optional) Add unique constraint to prevent duplicate client names:
   ```sql
   -- Execute contents of add_name_unique_constraint.sql
   ALTER TABLE clients 
   ADD CONSTRAINT clients_name_unique UNIQUE (name);
   ```
3. Insert the Autonomite client:
   ```sql
   -- Execute contents of insert_autonomite_client_simple.sql
   ```
4. Note the returned client ID - save it for reference

### Step 3: Verify the setup

Run the following command to verify:
```bash
cd /root/autonomite-agent-platform/scripts
source script_env/bin/activate
python3 migrate_autonomite_client.py
```

This will check if the client exists and display its ID.

## Next Steps

Once the database is set up, the development can continue with:
1. Implementing the ClientConnectionManager
2. Refactoring services for multi-tenancy
3. Updating API endpoints
4. Running comprehensive tests