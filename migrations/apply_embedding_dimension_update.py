#!/usr/bin/env python3
"""
Script to update embedding dimensions for Live Free Academy client
This script handles the migration from 4096 to 1024 dimensional embeddings
"""

import asyncio
import logging
import sys
from typing import Optional
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EmbeddingDimensionMigrator:
    def __init__(self, supabase_url: str, service_role_key: str):
        self.supabase: Client = create_client(supabase_url, service_role_key)
        
    def check_table_structure(self):
        """Check current table structure"""
        try:
            # Check if documents_with_embeddings table exists
            result = self.supabase.rpc('check_table_exists', {'table_name': 'documents_with_embeddings'}).execute()
            
            if not result.data:
                logger.info("Table documents_with_embeddings does not exist")
                return False
                
            # Get column information
            query = """
                SELECT column_name, data_type, udt_name
                FROM information_schema.columns
                WHERE table_name = 'documents_with_embeddings'
                AND column_name = 'embedding';
            """
            result = self.supabase.rpc('execute_sql', {'query': query}).execute()
            
            if result.data:
                logger.info(f"Current embedding column info: {result.data}")
                return True
            else:
                logger.warning("No embedding column found")
                return False
                
        except Exception as e:
            logger.error(f"Error checking table structure: {e}")
            return False
    
    def backup_embeddings(self):
        """Create a backup of current embeddings"""
        try:
            logger.info("Creating backup of current embeddings...")
            
            # Create backup table
            backup_query = """
                CREATE TABLE IF NOT EXISTS documents_embeddings_backup AS 
                SELECT id, embedding 
                FROM documents_with_embeddings 
                WHERE embedding IS NOT NULL;
            """
            self.supabase.rpc('execute_sql', {'query': backup_query}).execute()
            
            # Count backed up records
            count_query = "SELECT COUNT(*) as count FROM documents_embeddings_backup;"
            result = self.supabase.rpc('execute_sql', {'query': count_query}).execute()
            
            count = result.data[0]['count'] if result.data else 0
            logger.info(f"Backed up {count} embedding records")
            
            return True
            
        except Exception as e:
            logger.error(f"Error creating backup: {e}")
            return False
    
    def update_embedding_dimensions(self):
        """Update embedding dimensions from 4096 to 1024"""
        try:
            logger.info("Starting embedding dimension update...")
            
            # Step 1: Add new column
            logger.info("Adding new embedding column with 1024 dimensions...")
            add_column_query = """
                ALTER TABLE documents_with_embeddings 
                ADD COLUMN IF NOT EXISTS embedding_1024 vector(1024);
            """
            self.supabase.rpc('execute_sql', {'query': add_column_query}).execute()
            
            # Step 2: Copy and truncate embeddings
            logger.info("Copying and truncating embeddings to 1024 dimensions...")
            update_query = """
                UPDATE documents_with_embeddings 
                SET embedding_1024 = 
                    CASE 
                        WHEN embedding IS NOT NULL THEN 
                            substring(embedding::text from 2 for 1024*16)::vector
                        ELSE NULL
                    END
                WHERE embedding IS NOT NULL;
            """
            self.supabase.rpc('execute_sql', {'query': update_query}).execute()
            
            # Step 3: Drop old column
            logger.info("Dropping old embedding column...")
            drop_query = """
                ALTER TABLE documents_with_embeddings 
                DROP COLUMN IF EXISTS embedding;
            """
            self.supabase.rpc('execute_sql', {'query': drop_query}).execute()
            
            # Step 4: Rename new column
            logger.info("Renaming new column to embedding...")
            rename_query = """
                ALTER TABLE documents_with_embeddings 
                RENAME COLUMN embedding_1024 TO embedding;
            """
            self.supabase.rpc('execute_sql', {'query': rename_query}).execute()
            
            # Step 5: Create index
            logger.info("Creating index on new embedding column...")
            index_query = """
                CREATE INDEX IF NOT EXISTS documents_embedding_idx 
                ON documents_with_embeddings 
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100);
            """
            self.supabase.rpc('execute_sql', {'query': index_query}).execute()
            
            logger.info("Embedding dimension update completed successfully!")
            return True
            
        except Exception as e:
            logger.error(f"Error updating embedding dimensions: {e}")
            return False
    
    def verify_migration(self):
        """Verify the migration was successful"""
        try:
            # Check new column structure
            query = """
                SELECT 
                    column_name,
                    data_type,
                    character_maximum_length
                FROM information_schema.columns
                WHERE table_name = 'documents_with_embeddings'
                AND column_name = 'embedding';
            """
            result = self.supabase.rpc('execute_sql', {'query': query}).execute()
            
            if result.data:
                logger.info(f"New embedding column info: {result.data}")
                
                # Check a sample embedding
                sample_query = """
                    SELECT 
                        id,
                        array_length(embedding::float[], 1) as dimension
                    FROM documents_with_embeddings
                    WHERE embedding IS NOT NULL
                    LIMIT 1;
                """
                sample_result = self.supabase.rpc('execute_sql', {'query': sample_query}).execute()
                
                if sample_result.data:
                    dimension = sample_result.data[0]['dimension']
                    logger.info(f"Sample embedding dimension: {dimension}")
                    return dimension == 1024
                    
            return False
            
        except Exception as e:
            logger.error(f"Error verifying migration: {e}")
            return False


async def main():
    """Main migration function"""
    # Configuration for Live Free Academy
    # These should be set as environment variables in production
    import os
    
    SUPABASE_URL = os.getenv('LIVE_FREE_SUPABASE_URL', '')
    SERVICE_ROLE_KEY = os.getenv('LIVE_FREE_SERVICE_ROLE_KEY', '')
    
    if not SUPABASE_URL or not SERVICE_ROLE_KEY:
        logger.error("Please set LIVE_FREE_SUPABASE_URL and LIVE_FREE_SERVICE_ROLE_KEY environment variables")
        sys.exit(1)
    
    migrator = EmbeddingDimensionMigrator(SUPABASE_URL, SERVICE_ROLE_KEY)
    
    # Check current structure
    if not migrator.check_table_structure():
        logger.error("Table structure check failed. Aborting migration.")
        sys.exit(1)
    
    # Create backup
    if not migrator.backup_embeddings():
        logger.error("Backup creation failed. Aborting migration.")
        sys.exit(1)
    
    # Perform migration
    if not migrator.update_embedding_dimensions():
        logger.error("Migration failed. Please check the logs and restore from backup if needed.")
        sys.exit(1)
    
    # Verify migration
    if migrator.verify_migration():
        logger.info("Migration completed and verified successfully!")
    else:
        logger.warning("Migration completed but verification failed. Please check manually.")


if __name__ == "__main__":
    asyncio.run(main())