from django.db import migrations

FORWARD = """
CREATE FUNCTION audit_log_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (% blocked)', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_update_delete
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation();
"""

REVERSE = """
DROP TRIGGER audit_log_no_update_delete ON audit_log;
DROP FUNCTION audit_log_reject_mutation();
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
