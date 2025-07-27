import os
import psycopg2
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def execute_sql_from_file(file_path, conn_params):
    """
    Executes a SQL script from a file on the specified database.
    """
    try:
        with psycopg2.connect(**conn_params) as conn:
            with conn.cursor() as cursor:
                with open(file_path, 'r') as f:
                    sql_script = f.read()
                    cursor.execute(sql_script)
                logging.info(f"Successfully executed SQL script from '{file_path}'")
    except psycopg2.Error as e:
        logging.error(f"Database error while executing {file_path}: {e}")
        raise
    except FileNotFoundError:
        logging.error(f"SQL script file not found: {file_path}")
        raise
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        raise

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

    # Construct PostgreSQL connection string from Supabase credentials
    db_url = os.getenv("SUPABASE_URL")
    db_password = os.getenv("SUPABASE_SERVICE_ROLE_KEY") # This is not the password, but used for connection
    
    if not db_url or not db_password:
        logging.critical("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in the .env file.")
        return

    # This is a reconstruction based on typical patterns.
    try:
        # Correctly parse the hostname from the Supabase URL
        # Example URL: https://eukudpgfpihxsypulopm.supabase.co
        from urllib.parse import urlparse
        parsed_url = urlparse(db_url)
        host = parsed_url.netloc

        if not host:
            raise ValueError("Could not parse hostname from SUPABASE_URL")
        
        conn_params = {
            "dbname": "postgres",
            "user": "postgres",
            "password": db_password,
            "host": host,
            "port": "5432"
        }

        # Path to the SQL script
        sql_file = 'create_clients_table.sql'

        logging.info("Connecting to the Sidekick Forge database to set up schema...")
        execute_sql_from_file(sql_file, conn_params)
        logging.info("Database schema setup complete.")

    except Exception as e:
        logging.error(f"Failed to set up database schema: {e}")

if __name__ == "__main__":
    main() 