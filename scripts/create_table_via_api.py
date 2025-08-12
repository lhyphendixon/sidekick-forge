import os
import httpx
from dotenv import load_dotenv
import logging
import base64

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    # Load environment variables
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dotenv_path = os.path.join(script_dir, '..', '.env')
    load_dotenv(dotenv_path=dotenv_path)

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    if not supabase_url or not supabase_key:
        logging.critical("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
        return
    
    # Read SQL script
    sql_file = os.path.join(script_dir, 'create_clients_table.sql')
    with open(sql_file, 'r') as f:
        sql_script = f.read()
    
    logging.info("Since Supabase doesn't provide direct SQL execution via API,")
    logging.info("you need to execute this SQL in the Supabase Dashboard:")
    logging.info("1. Go to https://supabase.com/dashboard")
    logging.info("2. Select your 'Sidekick Forge' project")
    logging.info("3. Go to SQL Editor")
    logging.info("4. Paste and run the following SQL:\n")
    print("=" * 80)
    print(sql_script)
    print("=" * 80)
    
    # For now, let's create a simple setup script that checks if table exists
    from supabase import create_client, Client
    
    try:
        supabase: Client = create_client(supabase_url, supabase_key)
        result = supabase.table('clients').select('id').limit(1).execute()
        logging.info("\n✅ Table 'clients' already exists!")
    except Exception as e:
        if "relation" in str(e) and "does not exist" in str(e):
            logging.info("\n⚠️  Table 'clients' does not exist yet.")
            logging.info("Please execute the SQL above in your Supabase dashboard.")

if __name__ == "__main__":
    main()