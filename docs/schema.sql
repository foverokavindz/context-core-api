-- ContextCore - PostgreSQL schema
--
-- Generated from the SQLAlchemy mappers in app/entities. Regenerate rather
-- than edit by hand: the entities are the source of truth.

-- ==========================================================================
-- EXTENSIONS
-- ==========================================================================

-- chunks.embedding is vector(1536); pgvector must be present before the table.
CREATE EXTENSION IF NOT EXISTS vector;

-- ==========================================================================
-- ENUM TYPES
-- ==========================================================================

CREATE TYPE application_role AS ENUM (
    'SUPER_ADMIN',
    'HR',
    'EMPLOYEE'
);

CREATE TYPE credential_type AS ENUM (
    'GITHUB',
    'JIRA',
    'CONFLUENCE',
    'SLACK'
);

CREATE TYPE document_status AS ENUM (
    'UPLOADED',
    'PROCESSING',
    'READY',
    'FAILED'
);

CREATE TYPE member_role AS ENUM (
    'TEAM_LEAD',
    'TEAM_MEMBER'
);

CREATE TYPE message_role AS ENUM (
    'USER',
    'ASSISTANT'
);

CREATE TYPE resource_access_scope AS ENUM (
    'TEAM',
    'DEPARTMENT',
    'ORGANIZATION'
);

CREATE TYPE resource_type AS ENUM (
    'GITHUB_FILE',
    'JIRA_ISSUE',
    'CONFLUENCE_PAGE',
    'SLACK_MESSAGE',
    'DOCUMENT'
);

CREATE TYPE source_status AS ENUM (
    'ACTIVE',
    'INACTIVE',
    'ERROR'
);

CREATE TYPE source_type AS ENUM (
    'GITHUB',
    'JIRA',
    'CONFLUENCE',
    'SLACK'
);

CREATE TYPE sync_run_status AS ENUM (
    'PENDING',
    'RUNNING',
    'COMPLETED',
    'FAILED'
);

-- ==========================================================================
-- TABLES
-- ==========================================================================

-- Listed in dependency order: every table's foreign key targets are created before it.

CREATE TABLE departments (
    id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_departments PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_departments_name ON departments (name);

CREATE TABLE job_titles (
    id UUID NOT NULL,
    department_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_job_titles PRIMARY KEY (id),
    CONSTRAINT uq_job_titles_department_id_name UNIQUE (department_id, name),
    CONSTRAINT fk_job_titles_department_id_departments FOREIGN KEY(department_id) REFERENCES departments (id)
);

CREATE TABLE users (
    id UUID NOT NULL,
    email VARCHAR(320) NOT NULL,
    username VARCHAR(150),
    password_hash VARCHAR(255) NOT NULL,
    first_name VARCHAR(255) NOT NULL,
    last_name VARCHAR(255) NOT NULL,
    department_id UUID,
    job_title_id UUID,
    application_role application_role DEFAULT 'EMPLOYEE' NOT NULL,
    is_active BOOLEAN DEFAULT true NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_users PRIMARY KEY (id),
    CONSTRAINT uq_users_username UNIQUE (username),
    CONSTRAINT fk_users_department_id_departments FOREIGN KEY(department_id) REFERENCES departments (id),
    CONSTRAINT fk_users_job_title_id_job_titles FOREIGN KEY(job_title_id) REFERENCES job_titles (id)
);

CREATE INDEX ix_users_department_id ON users (department_id);
CREATE UNIQUE INDEX ix_users_email ON users (email);
CREATE INDEX ix_users_job_title_id ON users (job_title_id);

CREATE TABLE chat_sessions (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    title VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_chat_sessions PRIMARY KEY (id),
    CONSTRAINT fk_chat_sessions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_chat_sessions_user_id ON chat_sessions (user_id);

CREATE TABLE documents (
    id UUID NOT NULL,
    uploaded_by_user_id UUID NOT NULL,
    original_file_name VARCHAR(512) NOT NULL,
    display_name VARCHAR(512),
    mime_type VARCHAR(255),
    file_size BIGINT NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    checksum VARCHAR(64),
    status document_status DEFAULT 'UPLOADED' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_documents PRIMARY KEY (id),
    CONSTRAINT fk_documents_uploaded_by_user_id_users FOREIGN KEY(uploaded_by_user_id) REFERENCES users (id)
);

CREATE INDEX ix_documents_checksum ON documents (checksum);
CREATE INDEX ix_documents_status ON documents (status);
CREATE INDEX ix_documents_uploaded_by_user_id ON documents (uploaded_by_user_id);

CREATE TABLE teams (
    id UUID NOT NULL,
    department_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_by_user_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_teams PRIMARY KEY (id),
    CONSTRAINT uq_teams_department_id_name UNIQUE (department_id, name),
    CONSTRAINT fk_teams_department_id_departments FOREIGN KEY(department_id) REFERENCES departments (id),
    CONSTRAINT fk_teams_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id)
);

CREATE INDEX ix_teams_created_by_user_id ON teams (created_by_user_id);

CREATE TABLE chat_session_messages (
    id UUID NOT NULL,
    chat_session_id UUID NOT NULL,
    role message_role NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_chat_session_messages PRIMARY KEY (id),
    CONSTRAINT fk_chat_session_messages_chat_session_id_chat_sessions FOREIGN KEY(chat_session_id) REFERENCES chat_sessions (id)
);

CREATE INDEX ix_chat_session_messages_chat_session_id ON chat_session_messages (chat_session_id);

CREATE TABLE source_credentials (
    id UUID NOT NULL,
    team_id UUID NOT NULL,
    credential_type credential_type NOT NULL,
    secret_reference VARCHAR(512),
    encrypted_secret TEXT,
    credential_metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_source_credentials PRIMARY KEY (id),
    CONSTRAINT fk_source_credentials_team_id_teams FOREIGN KEY(team_id) REFERENCES teams (id)
);

CREATE INDEX ix_source_credentials_team_id ON source_credentials (team_id);

CREATE TABLE team_members (
    id UUID NOT NULL,
    team_id UUID NOT NULL,
    user_id UUID NOT NULL,
    member_role member_role DEFAULT 'TEAM_MEMBER' NOT NULL,
    joined_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_team_members PRIMARY KEY (id),
    CONSTRAINT fk_team_members_team_id_teams FOREIGN KEY(team_id) REFERENCES teams (id),
    CONSTRAINT uq_team_members_user_id UNIQUE (user_id),
    CONSTRAINT fk_team_members_user_id_users FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_team_members_team_id ON team_members (team_id);

CREATE TABLE external_data_sources (
    id UUID NOT NULL,
    team_id UUID NOT NULL,
    credential_id UUID,
    created_by_user_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    source_type source_type NOT NULL,
    status source_status DEFAULT 'ACTIVE' NOT NULL,
    config JSONB,
    token VARCHAR(2048),
    last_synced_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_external_data_sources PRIMARY KEY (id),
    CONSTRAINT fk_external_data_sources_team_id_teams FOREIGN KEY(team_id) REFERENCES teams (id),
    CONSTRAINT fk_external_data_sources_credential_id_source_credentials FOREIGN KEY(credential_id) REFERENCES source_credentials (id),
    CONSTRAINT fk_external_data_sources_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id)
);

CREATE INDEX ix_external_data_sources_created_by_user_id ON external_data_sources (created_by_user_id);
CREATE INDEX ix_external_data_sources_credential_id ON external_data_sources (credential_id);
CREATE INDEX ix_external_data_sources_source_type ON external_data_sources (source_type);
CREATE INDEX ix_external_data_sources_status ON external_data_sources (status);
CREATE INDEX ix_external_data_sources_team_id ON external_data_sources (team_id);

CREATE TABLE resources (
    id UUID NOT NULL,
    external_data_source_id UUID,
    document_id UUID,
    resource_type resource_type NOT NULL,
    external_id VARCHAR(512),
    title VARCHAR(1024),
    version_key VARCHAR(255),
    access_scope resource_access_scope NOT NULL,
    team_id UUID,
    department_id UUID,
    resource_metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_resources PRIMARY KEY (id),
    CONSTRAINT uq_resources_external_data_source_id_external_id UNIQUE (external_data_source_id, external_id),
    CONSTRAINT ck_resources_single_origin CHECK ((external_data_source_id IS NULL) <> (document_id IS NULL)),
    CONSTRAINT fk_resources_external_data_source_id_external_data_sources FOREIGN KEY(external_data_source_id) REFERENCES external_data_sources (id),
    CONSTRAINT fk_resources_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id),
    CONSTRAINT fk_resources_team_id_teams FOREIGN KEY(team_id) REFERENCES teams (id),
    CONSTRAINT fk_resources_department_id_departments FOREIGN KEY(department_id) REFERENCES departments (id)
);

CREATE INDEX ix_resources_access_scope ON resources (access_scope);
CREATE INDEX ix_resources_department_id ON resources (department_id);
CREATE UNIQUE INDEX ix_resources_document_id ON resources (document_id);
CREATE INDEX ix_resources_external_id ON resources (external_id);
CREATE INDEX ix_resources_resource_type ON resources (resource_type);
CREATE INDEX ix_resources_team_id ON resources (team_id);

CREATE TABLE sync_runs (
    id UUID NOT NULL,
    external_data_source_id UUID NOT NULL,
    status sync_run_status DEFAULT 'PENDING' NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    resources_processed INTEGER DEFAULT '0' NOT NULL,
    chunks_created INTEGER DEFAULT '0' NOT NULL,
    chunks_updated INTEGER DEFAULT '0' NOT NULL,
    chunks_deleted INTEGER DEFAULT '0' NOT NULL,
    error_message TEXT,
    run_metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_sync_runs PRIMARY KEY (id),
    CONSTRAINT fk_sync_runs_external_data_source_id_external_data_sources FOREIGN KEY(external_data_source_id) REFERENCES external_data_sources (id)
);

CREATE INDEX ix_sync_runs_external_data_source_id ON sync_runs (external_data_source_id);
CREATE INDEX ix_sync_runs_status ON sync_runs (status);

CREATE TABLE chunks (
    id UUID NOT NULL,
    external_data_source_id UUID,
    external_id VARCHAR(512),
    chunk_index INTEGER NOT NULL,
    chunk_type VARCHAR(255),
    content TEXT NOT NULL,
    embedding VECTOR(1536),
    embedding_model VARCHAR(255),
    chunk_metadata JSONB,
    access_scope resource_access_scope NOT NULL,
    team_id UUID,
    department_id UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_chunks PRIMARY KEY (id),
    CONSTRAINT fk_chunks_resource FOREIGN KEY(external_data_source_id, external_id) REFERENCES resources (external_data_source_id, external_id),
    CONSTRAINT uq_chunks_external_data_source_id_external_id_chunk_index UNIQUE (external_data_source_id, external_id, chunk_index),
    CONSTRAINT fk_chunks_team_id_teams FOREIGN KEY(team_id) REFERENCES teams (id),
    CONSTRAINT fk_chunks_department_id_departments FOREIGN KEY(department_id) REFERENCES departments (id)
);

CREATE INDEX ix_chunks_access_scope ON chunks (access_scope);
CREATE INDEX ix_chunks_chunk_type ON chunks (chunk_type);
CREATE INDEX ix_chunks_department_id ON chunks (department_id);
CREATE INDEX ix_chunks_team_id ON chunks (team_id);

CREATE TABLE citations (
    id UUID NOT NULL,
    chat_message_id UUID NOT NULL,
    chunk_id UUID NOT NULL,
    resource_id UUID NOT NULL,
    citation_order INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_citations PRIMARY KEY (id),
    CONSTRAINT uq_citations_chat_message_id_citation_order UNIQUE (chat_message_id, citation_order),
    CONSTRAINT fk_citations_chat_message_id_chat_session_messages FOREIGN KEY(chat_message_id) REFERENCES chat_session_messages (id),
    CONSTRAINT fk_citations_chunk_id_chunks FOREIGN KEY(chunk_id) REFERENCES chunks (id),
    CONSTRAINT fk_citations_resource_id_resources FOREIGN KEY(resource_id) REFERENCES resources (id)
);

CREATE INDEX ix_citations_chunk_id ON citations (chunk_id);
CREATE INDEX ix_citations_resource_id ON citations (resource_id);
