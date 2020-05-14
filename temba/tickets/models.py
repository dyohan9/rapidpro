from abc import ABCMeta

from smartmin.models import SmartModel

from django.contrib.postgres.fields import JSONField
from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import Contact
from temba.orgs.models import Org
from temba.utils.uuid import uuid4


class TicketerType(metaclass=ABCMeta):
    """
    TicketerType is our abstract base type for ticketers.
    """

    # the verbose name for this ticketer type
    name = None

    # the short code for this ticketer type (< 16 chars, lowercase)
    slug = None

    # the icon to show for this ticketer type
    icon = "icon-channel-external"

    def is_available(self):  # pragma: no cover
        """
        Determines whether this ticketer type is available
        """
        return True


class Ticketer(SmartModel):
    """
    A service that can open and close tickets
    """

    # our UUID
    uuid = models.UUIDField(default=uuid4)

    # the type of this ticketer
    ticketer_type = models.CharField(max_length=16)

    # the org this ticketer is connected to
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="ticketers")

    # a name for this ticketer
    name = models.CharField(max_length=64)

    # the configuration options
    config = JSONField()

    @classmethod
    def create(cls, org, user, ticketer_type, name, config):
        return cls.objects.create(
            uuid=uuid4(),
            ticketer_type=ticketer_type,
            name=name,
            config=config,
            org=org,
            created_by=user,
            modified_by=user,
        )

    @classmethod
    def get_types(cls):  # pragma: no cover
        """
        Returns the possible types available for ticketers
        """
        from .types import TYPES

        return TYPES.values()

    def get_type(self):  # pragma: no cover
        """
        Returns the type instance
        """
        from .types import TYPES

        return TYPES[self.ticketer_type]

    def release(self):
        """
        Releases this, closing all associated tickets in the process
        """
        used_by = self.dependent_flows.count()
        if used_by > 0:
            raise ValueError(f"Cannot delete ticketer: {self.name}, used by {used_by} flows")

        for ticket in self.tickets.all():
            ticket.close()

        self.is_active = False
        self.save(update_fields=("is_active", "modified_on"))


class Ticket(models.Model):
    """
    A ticket represents a contact-initiated question or dialog.
    """

    STATUS_OPEN = "O"
    STATUS_CLOSED = "C"
    STATUS_CHOICES = ((STATUS_OPEN, _("Open")), (STATUS_CLOSED, _("Closed")))

    # our UUID
    uuid = models.UUIDField(unique=True, default=uuid4)

    # the organization this ticket belongs to
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="tickets")

    # the ticketer that manages this ticket
    ticketer = models.ForeignKey(Ticketer, on_delete=models.PROTECT, related_name="tickets")

    # the contact this ticket is tied to
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="tickets")

    # the subject of the ticket
    subject = models.TextField()

    # the body of the ticket
    body = models.TextField()

    # the external id of the ticket
    external_id = models.CharField(null=True, max_length=255)

    # any configuration attributes for this ticket
    config = JSONField(null=True)

    # the status of this ticket, one of open, closed, expired
    status = models.CharField(max_length=1, choices=STATUS_CHOICES)

    # when this ticket was opened
    opened_on = models.DateTimeField(default=timezone.now)

    # when this ticket was last modified
    modified_on = models.DateTimeField(default=timezone.now)

    # when this ticket was closed
    closed_on = models.DateTimeField(null=True)

    def close(self):
        """
        Closes the ticket
        """
        self.status = Ticket.STATUS_CLOSED
        self.closed_on = timezone.now()
        self.save(update_fields=("status", "modified_on", "closed_on"))
