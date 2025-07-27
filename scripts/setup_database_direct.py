import os
import psycopg2
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    """
    Main function to set up the Sidekick Forge database schema.
    """
    # Get the directory where the script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Construct the path to the .env file (one directory up)
    dotenv_path = os.path.join(script_dir, '..', '.env')

    # Load environment variables from .env file
    load_dotenv(dotenv_path=dotenv_path)

    # For Supabase, we need to construct the database URL
    # The pattern is: postgres://postgres:[YOUR-PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
    
    supabase_url = os.getenv("SUPABASE_URL")
    if not supabase_url:
        logging.critical("SUPABASE_URL must be set in the .env file.")
        return
    
    # Extract project ref from URL
    # https://eukudpgfpihxsypulopm.supabase.co -> eukudpgfpihxsypulopm
    project_ref = supabase_url.split('//')[1].split('.')[0]
    
    # For now, we'll need the actual database password
    # This should be obtained from Supabase dashboard
    logging.info(f"Project reference: {project_ref}")
    logging.info("To execute the database setup:")
    logging.info("1. Go to your Supabase dashboard")
    logging.info("2. Navigate to Settings > Database")
    logging.info("3. Copy the database password")
    logging.info("4. Go to SQL Editor in the dashboard")
    logging.info("5. Execute the following SQL:\n")
    
    sql_file = os.path.join(script_dir, 'create_clients_table.sql')
    with open(sql_file, 'r') as f:
        print(f.read())

if __name__ == "__main__":
    main()