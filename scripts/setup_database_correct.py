import os
import logging
from supabase import create_client, Client
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    """
    Main function to set up the Sidekick Forge database schema using Supabase client.
    """
    # Get the directory where the script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Construct the path to the .env file (one directory up)
    dotenv_path = os.path.join(script_dir, '..', '.env')

    # Load environment variables from .env file
    load_dotenv(dotenv_path=dotenv_path)

    # Get Supabase credentials
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    if not supabase_url or not supabase_key:
        logging.critical("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in the .env file.")
        return

    try:
        logging.info("Connecting to the Sidekick Forge database...")
        
        # Create Supabase client with service role key
        supabase: Client = create_client(supabase_url, supabase_key)
        
        # Read the SQL script
        sql_file = os.path.join(script_dir, 'create_clients_table.sql')
        with open(sql_file, 'r') as f:
            sql_script = f.read()
        
        # Execute the SQL script using Supabase RPC
        # We'll execute it as raw SQL
        logging.info("Creating clients table...")
        
        # Supabase doesn't provide direct SQL execution via Python client
        # We need to check if the table exists first
        result = supabase.table('clients').select('id').limit(1).execute()
        logging.info("Table 'clients' already exists!")
        
    except Exception as e:
        if "doesn't exist" in str(e) or "relation" in str(e) and "does not exist" in str(e):
            logging.error("Table doesn't exist yet. Please create it via Supabase dashboard SQL editor.")
            logging.info(f"SQL script location: {sql_file}")
            logging.info("Copy and execute the following SQL in your Supabase dashboard:")
            with open(sql_file, 'r') as f:
                print("\n" + f.read())
        else:
            logging.error(f"Failed to connect to database: {e}")

if __name__ == "__main__":
    main()