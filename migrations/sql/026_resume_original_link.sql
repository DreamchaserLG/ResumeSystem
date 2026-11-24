ALTER TABLE resume_document
    ADD COLUMN source_kind text NOT NULL DEFAULT 'file'
        CHECK (source_kind IN ('file','reconstructed')),
    ADD COLUMN original_filename text;

ALTER TABLE application ADD COLUMN original_url text GENERATED ALWAYS AS (
    CASE WHEN active_version_id IS NOT NULL AND deleted_at IS NULL THEN
        '/api/v1/hr/applications/' || id::text || '/original?version=' || active_version_id::text
    END
) STORED;

COMMENT ON COLUMN application.original_url IS
    'Authenticated URL for the file of the active resume version. Append inline=true for a PDF preview.';
