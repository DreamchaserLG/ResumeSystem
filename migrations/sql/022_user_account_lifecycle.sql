-- Removed accounts retain their identity for audit and review foreign keys.
ALTER TABLE app_user DROP CONSTRAINT app_user_status_check;
ALTER TABLE app_user ADD CONSTRAINT app_user_status_check
    CHECK (status IN ('active','disabled','deleted'));
