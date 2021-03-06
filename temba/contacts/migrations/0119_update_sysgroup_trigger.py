# Generated by Django 2.2.10 on 2020-08-24 21:18

from django.db import migrations

SQL = """
----------------------------------------------------------------------
-- Trigger procedure to update contact system groups on column changes
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_contact_system_groups() RETURNS TRIGGER AS $$
BEGIN
  -- new contact added
  IF TG_OP = 'INSERT' AND NEW.is_active THEN
    IF NEW.status = 'A' THEN
      PERFORM contact_toggle_system_group(NEW, 'A', true);
    ELSIF NEW.status = 'B' THEN
      PERFORM contact_toggle_system_group(NEW, 'B', true);
    ELSIF NEW.status = 'S' THEN
      PERFORM contact_toggle_system_group(NEW, 'S', true);
    ELSIF NEW.status = 'V' THEN
      PERFORM contact_toggle_system_group(NEW, 'V', true);
    END IF;
  END IF;

  -- existing contact updated
  IF TG_OP = 'UPDATE' THEN
    -- do nothing for inactive contacts
    IF NOT OLD.is_active AND NOT NEW.is_active THEN
      RETURN NULL;
    END IF;

    IF OLD.status != NEW.status THEN
      PERFORM contact_toggle_system_group(NEW, OLD.status, false);
      PERFORM contact_toggle_system_group(NEW, NEW.status, true);
    END IF;

    -- is being released
    IF OLD.is_active AND NOT NEW.is_active THEN
      PERFORM contact_toggle_system_group(NEW, 'A', false);
      PERFORM contact_toggle_system_group(NEW, 'B', false);
      PERFORM contact_toggle_system_group(NEW, 'S', false);
      PERFORM contact_toggle_system_group(NEW, 'V', false);
    END IF;

    -- is being unreleased
    IF NOT OLD.is_active AND NEW.is_active THEN
      PERFORM contact_toggle_system_group(NEW, NEW.status, true);
    END IF;
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;"""


class Migration(migrations.Migration):

    dependencies = [
        ("contacts", "0118_archived_sys_group"),
    ]

    operations = [migrations.RunSQL(SQL)]
