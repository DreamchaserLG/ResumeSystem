ALTER TABLE resume_record ADD COLUMN review_anchor text;
ALTER TABLE resume_preview_page ADD COLUMN photo_object_key text;

-- Store references to reviewed versions, without duplicating corrected values.
CREATE TABLE application_field_review (
    application_id bigint REFERENCES application(id) ON DELETE CASCADE,
    review_anchor text NOT NULL,
    field_key text NOT NULL,
    action text NOT NULL CHECK (action IN ('correct','dismiss')),
    version_id bigint REFERENCES resume_version(id) ON DELETE CASCADE,
    record_no integer NOT NULL,
    PRIMARY KEY(application_id,review_anchor,field_key),
    FOREIGN KEY(version_id,record_no) REFERENCES resume_record(version_id,record_no) ON DELETE CASCADE
);
ALTER TABLE application_field_review ENABLE ROW LEVEL SECURITY;
ALTER TABLE application_field_review FORCE ROW LEVEL SECURITY;
