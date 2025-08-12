import os
import logging
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    """
    Migrate Autonomite as the first client in the Sidekick Forge platform.
    """
    # Load environment variables
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dotenv_path = os.path.join(script_dir, '..', '.env')
    load_dotenv(dotenv_path=dotenv_path)

    # Sidekick Forge platform credentials
    platform_url = os.getenv("SUPABASE_URL")
    platform_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    if not platform_url or not platform_key:
        logging.critical("Platform credentials not found in .env file.")
        return
    
    try:
        # Connect to Sidekick Forge platform database
        logging.info("Connecting to Sidekick Forge platform database...")
        platform_db: Client = create_client(platform_url, platform_key)
        
        # Check if Autonomite client already exists
        existing = platform_db.table('clients').select('*').eq('name', 'Autonomite').execute()
        
        if existing.data:
            logging.info("✅ Autonomite client already exists!")
            logging.info(f"Client ID: {existing.data[0]['id']}")
            return
        
        # Prepare Autonomite client data
        # These are the existing Autonomite credentials that need to be migrated
        autonomite_data = {
            'name': 'Autonomite',
            'supabase_url': 'https://yuowazxcxwhczywurmmw.supabase.co',
            'supabase_service_role_key': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY',
            
            # Autonomite's LiveKit credentials
            'livekit_url': 'wss://autonomite-m9fsc2wp.livekit.cloud',
            'livekit_api_key': 'APIrZaVVGtq5PCX',
            'livekit_api_secret': 'mRj96UaZFIA8ECFqBK9kIZYFlfW0FHWYZz7Yi3loJ0V',
            
            # Placeholder API keys - these should be updated with actual keys
            'openai_api_key': None,  # To be filled from actual client settings
            'groq_api_key': None,
            'deepgram_api_key': None,
            'elevenlabs_api_key': None,
            'cartesia_api_key': None,
            'speechify_api_key': None,
            'deepinfra_api_key': None,
            'replicate_api_key': None,
            'novita_api_key': None,
            'cohere_api_key': None,
            'siliconflow_api_key': None,
            'jina_api_key': None,
            'anthropic_api_key': None,
            
            'additional_settings': {
                'is_first_client': True,
                'migration_date': datetime.now().isoformat(),
                'notes': 'Migrated from original Autonomite setup'
            }
        }
        
        # Insert Autonomite as a client
        logging.info("Inserting Autonomite as a client in Sidekick Forge platform...")
        result = platform_db.table('clients').insert(autonomite_data).execute()
        
        if result.data:
            logging.info("✅ Successfully migrated Autonomite client!")
            logging.info(f"Client ID: {result.data[0]['id']}")
            
            # Save the client ID for reference
            client_id_file = os.path.join(script_dir, 'autonomite_client_id.txt')
            with open(client_id_file, 'w') as f:
                f.write(result.data[0]['id'])
            logging.info(f"Client ID saved to: {client_id_file}")
        else:
            logging.error("Failed to insert Autonomite client.")
            
    except Exception as e:
        if "relation" in str(e) and "does not exist" in str(e):
            logging.error("❌ Table 'clients' does not exist!")
            logging.error("Please create the table first using the SQL script in the Supabase dashboard.")
            logging.error("Run: python3 create_table_via_api.py for instructions.")
        else:
            logging.error(f"Error: {e}")

if __name__ == "__main__":
    main()