-- Microsoft Foundry (Azure AI Inference) — root-cause analysis results
-- Populated by backend.api.ai / backend.ai.foundry_client.
-- Read by the "AI Insights" panel on the dashboard.

IF NOT EXISTS (
    SELECT 1 FROM sys.tables t
    JOIN sys.schemas s ON t.schema_id = s.schema_id
    WHERE s.name = 'iot' AND t.name = 'ai_insights'
)
BEGIN
    CREATE TABLE iot.ai_insights (
        insight_id              BIGINT IDENTITY(1,1) PRIMARY KEY,
        created_at              DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        device_id               NVARCHAR(200) NOT NULL,
        device_type             NVARCHAR(100) NULL,
        summary                 NVARCHAR(MAX) NULL,
        root_cause_device_id    NVARCHAR(200) NULL,
        root_cause_device_type  NVARCHAR(100) NULL,
        confidence              FLOAT NULL,
        severity                NVARCHAR(40) NULL,
        blast_radius_json       NVARCHAR(MAX) NULL,
        recommended_actions_json NVARCHAR(MAX) NULL,
        rationale               NVARCHAR(MAX) NULL,
        ok                      BIT NOT NULL DEFAULT 1,
        error                   NVARCHAR(MAX) NULL,
        model                   NVARCHAR(100) NULL,
        elapsed_s               FLOAT NULL,
        context_json            NVARCHAR(MAX) NULL,
        payload_json            NVARCHAR(MAX) NULL
    );
    CREATE INDEX ai_insights_device_idx    ON iot.ai_insights (device_id, created_at DESC);
    CREATE INDEX ai_insights_created_idx   ON iot.ai_insights (created_at DESC);
    CREATE INDEX ai_insights_severity_idx  ON iot.ai_insights (severity, created_at DESC);
END
GO
