-- Add unique constraint to the name column to prevent duplicate client names
ALTER TABLE clients 
ADD CONSTRAINT clients_name_unique UNIQUE (name);