import os
from supabase import create_client, Client
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    # Load environment variables
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dotenv_path = os.path.join(script_dir, '..', '.env')
    load_dotenv(dotenv_path=dotenv_path)

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    logging.info(f"Connecting to: {supabase_url}")
    
    try:
        # Create Supabase client
        supabase: Client = create_client(supabase_url, supabase_key)
        
        # Try to list tables by querying information_schema
        result = supabase.rpc('list_tables', {}).execute()
        logging.info(f"RPC result: {result}")
    except Exception as e:
        logging.error(f"Error: {e}")
        
        # Try a simple query
        try:
            # Check if we can query any table
            result = supabase.table('clients').select('*').limit(1).execute()
            logging.info("Successfully connected! Table 'clients' exists.")
            logging.info(f"Result: {result}")
        except Exception as e2:
            if "relation" in str(e2) and "does not exist" in str(e2):
                logging.info("Table 'clients' does not exist yet. This is expected.")
                logging.info("Connection to Supabase is working!")
            else:
                logging.error(f"Connection error: {e2}")

if __name__ == "__main__":
    main()