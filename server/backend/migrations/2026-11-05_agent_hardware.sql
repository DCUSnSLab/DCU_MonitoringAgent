-- GEN-1773: 하드웨어/설치 저장 구조 추가 (PostgreSQL)
-- 기존 ID, 보고 경로 및 이력은 변경하지 않는다. 기존 행의 자리 확정은 후속 이관에서 수행한다.
-- 신규 DB는 create_all, 기존 PostgreSQL은 이 스크립트 또는 앱 시작 시 초기화로 적용한다.
-- 실행: psql -v ON_ERROR_STOP=1 -U dcu -d dcu_monitoring -f 2026-11-05_agent_hardware.sql
-- 부분 인덱스가 동시 요청에서도 자리당 활성 설치를 최대 하나로 제한한다.
BEGIN;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS seat_confirmed BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS agent_cpus (
	physical_cores INTEGER,
	logical_processors INTEGER,
	id SERIAL NOT NULL,
	agent_pk INTEGER NOT NULL,
	device_key VARCHAR(512) NOT NULL,
	model VARCHAR(256),
	PRIMARY KEY (id),
	CONSTRAINT uq_agent_cpu_device UNIQUE (agent_pk, device_key),
	CONSTRAINT ck_cpu_cores_positive CHECK (physical_cores > 0),
	CONSTRAINT ck_cpu_threads_positive CHECK (logical_processors > 0),
	FOREIGN KEY(agent_pk) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_disks (
	capacity_bytes BIGINT,
	media_type VARCHAR(16),
	id SERIAL NOT NULL,
	agent_pk INTEGER NOT NULL,
	device_key VARCHAR(512) NOT NULL,
	model VARCHAR(256),
	PRIMARY KEY (id),
	CONSTRAINT uq_agent_disk_device UNIQUE (agent_pk, device_key),
	CONSTRAINT ck_disk_capacity_nonnegative CHECK (capacity_bytes >= 0),
	CONSTRAINT ck_disk_media_type CHECK (media_type IN ('ssd', 'hdd')),
	FOREIGN KEY(agent_pk) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_gpus (
	vendor VARCHAR(128),
	dedicated_memory_bytes BIGINT,
	gpu_type VARCHAR(16),
	id SERIAL NOT NULL,
	agent_pk INTEGER NOT NULL,
	device_key VARCHAR(512) NOT NULL,
	model VARCHAR(256),
	PRIMARY KEY (id),
	CONSTRAINT uq_agent_gpu_device UNIQUE (agent_pk, device_key),
	CONSTRAINT ck_gpu_memory_nonnegative CHECK (dedicated_memory_bytes >= 0),
	CONSTRAINT ck_gpu_type CHECK (gpu_type IN ('integrated', 'discrete')),
	FOREIGN KEY(agent_pk) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_hardware (
	agent_pk INTEGER NOT NULL,
	memory_total_bytes BIGINT,
	collected_at TIMESTAMP WITH TIME ZONE NOT NULL,
	synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (agent_pk),
	CONSTRAINT ck_hardware_memory_nonnegative CHECK (memory_total_bytes >= 0),
	FOREIGN KEY(agent_pk) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_id_aliases (
	legacy_agent_id VARCHAR(128) NOT NULL,
	agent_pk INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (legacy_agent_id),
	FOREIGN KEY(agent_pk) REFERENCES agents (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_agent_id_aliases_agent_pk ON agent_id_aliases (agent_pk);

CREATE TABLE IF NOT EXISTS agent_installations (
	installation_id UUID NOT NULL,
	agent_pk INTEGER NOT NULL,
	status VARCHAR(16) DEFAULT 'pending' NOT NULL,
	registered_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	approved_at TIMESTAMP WITH TIME ZONE,
	revoked_at TIMESTAMP WITH TIME ZONE,
	token_hash VARCHAR(64),
	PRIMARY KEY (installation_id),
	CONSTRAINT ck_installation_status CHECK (status IN ('pending', 'active', 'revoked')),
	CONSTRAINT ck_active_installation_credentials CHECK (status != 'active' OR (token_hash IS NOT NULL AND approved_at IS NOT NULL)),
	FOREIGN KEY(agent_pk) REFERENCES agents (id) ON DELETE CASCADE,
	UNIQUE (token_hash)
);
CREATE INDEX IF NOT EXISTS ix_agent_installations_agent_pk ON agent_installations (agent_pk);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_active_installation ON agent_installations (agent_pk) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS agent_network_adapters (
	id SERIAL NOT NULL,
	agent_pk INTEGER NOT NULL,
	device_key VARCHAR(512) NOT NULL,
	name VARCHAR(256),
	mac_address VARCHAR(64),
	adapter_type VARCHAR(16),
	is_physical BOOLEAN,
	PRIMARY KEY (id),
	CONSTRAINT uq_agent_network_device UNIQUE (agent_pk, device_key),
	CONSTRAINT ck_network_adapter_type CHECK (adapter_type IN ('ethernet', 'wifi', 'other')),
	FOREIGN KEY(agent_pk) REFERENCES agents (id) ON DELETE CASCADE
);

COMMIT;
