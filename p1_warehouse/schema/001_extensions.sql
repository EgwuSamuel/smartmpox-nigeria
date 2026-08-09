-- Enable required PostgreSQL extensions
CREATE EXTENSION IF NOT EXISTS postgis;           -- Spatial data types and functions
CREATE EXTENSION IF NOT EXISTS postgis_topology;  -- Topological spatial data
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";       -- UUID generation
CREATE EXTENSION IF NOT EXISTS pg_trgm;           -- Trigram text search
