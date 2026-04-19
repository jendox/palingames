from django.db import migrations


def migrate_guest_access_email_outboxes(apps, schema_editor):
    GuestAccessEmailOutbox = apps.get_model("access", "GuestAccessEmailOutbox")
    NotificationOutbox = apps.get_model("notifications", "NotificationOutbox")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Order = apps.get_model("orders", "Order")
    order_content_type = ContentType.objects.get_for_model(Order)

    notifications = [
        NotificationOutbox(
            created_at=outbox.created_at,
            updated_at=outbox.updated_at,
            channel="EMAIL",
            notification_type="guest_order_download",
            recipient=outbox.email,
            payload_encrypted=outbox.payload_encrypted,
            status=outbox.status,
            attempts=outbox.attempts,
            last_error=outbox.last_error,
            last_attempt_at=outbox.last_attempt_at,
            sent_at=outbox.sent_at,
            content_type=order_content_type,
            object_id=outbox.order_id,
        )
        for outbox in GuestAccessEmailOutbox.objects.all().iterator()
    ]
    NotificationOutbox.objects.bulk_create(notifications, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0004_guestaccessemailoutbox"),
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(migrate_guest_access_email_outboxes, migrations.RunPython.noop),
        migrations.DeleteModel(
            name="GuestAccessEmailOutbox",
        ),
    ]
