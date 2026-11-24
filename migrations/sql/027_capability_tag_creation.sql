-- Keep the curated display order for existing tags and date new tags at insertion.
ALTER TABLE capability_group ADD COLUMN created_at timestamptz;
ALTER TABLE capability_group ALTER COLUMN created_at SET DEFAULT clock_timestamp();
