import re
from typing import List
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.utils import timezone

from temba.contacts.models import URN, Contact, ContactField, ContactGroup, ContactURN
from temba.mailroom.client import MailroomClient, MailroomException
from temba.mailroom.modifiers import Modifier
from temba.orgs.models import Org
from temba.tickets.models import Ticket
from temba.utils import json


class Mocks:
    def __init__(self):
        self._parse_query = {}
        self._errors = []

    def parse_query(self, query, *, fields=None, allow_as_group=True, elastic_query=None, error: str = None):
        assert not (fields and error), "can't mock with both fields and error"

        def mock():
            if error:
                raise MailroomException("mr/parse_query", None, {"error": error})

            return {
                "query": query,
                "fields": list(fields),
                "allow_as_group": allow_as_group,
                "elastic_query": elastic_query or {"term": {"is_active": True}},
            }

        self._parse_query[query] = mock

    def error(self, msg: str):
        """
        Queues an error which will become a mailroom exception at the next client call
        """
        self._errors.append(msg)

    def _check_error(self, endpoint: str):
        if self._errors:
            raise MailroomException(endpoint, None, {"error": self._errors.pop(0)})


class TestClient(MailroomClient):
    def __init__(self, mocks: Mocks):
        self.mocks = mocks

        super().__init__(settings.MAILROOM_URL, settings.MAILROOM_AUTH_TOKEN)

    def contact_modify(self, org_id, user_id, contact_ids, modifiers: List[Modifier]):
        self.mocks._check_error("contact_modify")

        org = Org.objects.get(id=org_id)
        user = User.objects.get(id=user_id)
        contacts = org.contacts.filter(id__in=contact_ids)

        apply_modifiers(org, user, contacts, modifiers)

        return {c.id: {"contact": {}, "events": []} for c in contacts}

    def parse_query(self, org_id, query, group_uuid=""):
        self.mocks._check_error("parse_query")

        # if there's a mock for this query we use that
        mock = self.mocks._parse_query.get(query)
        if mock:
            return mock()

        # otherwise just approximate what mailroom would do
        tokens = [t.lower() for t in re.split(r"\W+", query) if t]
        fields = ContactField.all_fields.filter(org_id=org_id, key__in=tokens)

        return {
            "query": query,
            "fields": [f.key for f in fields],
            "allow_as_group": "id" not in tokens and "group" not in tokens,
            "elastic_query": {"term": {"is_active": True}},
        }

    def ticket_close(self, org_id, ticket_ids):
        self.mocks._check_error("ticket_close")

        tickets = Ticket.objects.filter(org_id=org_id, status=Ticket.STATUS_OPEN, id__in=ticket_ids)
        tickets.update(status=Ticket.STATUS_CLOSED)

        return {"changed_ids": [t.id for t in tickets]}

    def ticket_reopen(self, org_id, ticket_ids):
        self.mocks._check_error("ticket_reopen")

        tickets = Ticket.objects.filter(org_id=org_id, status=Ticket.STATUS_CLOSED, id__in=ticket_ids)
        tickets.update(status=Ticket.STATUS_OPEN)

        return {"changed_ids": [t.id for t in tickets]}


def mock_mailroom(f):
    """
    Convenience decorator to make a test method use a mocked version of the mailroom client
    """

    def wrapped(instance, *args, **kwargs):
        with patch("temba.mailroom.get_client") as mock_get_client:
            mocks = Mocks()
            mock_get_client.return_value = TestClient(mocks)
            return f(instance, mocks, *args, **kwargs)

    return wrapped


def apply_modifiers(org, user, contacts, modifiers: List):
    """
    Approximates mailroom applying modifiers but doesn't do dynamic group re-evaluation.
    """

    for mod in modifiers:
        fields = dict()
        clear_groups = False

        if mod.type == "name":
            fields = dict(name=mod.name)

        if mod.type == "language":
            fields = dict(language=mod.language)

        if mod.type == "field":
            for c in contacts:
                update_field_locally(user, c, mod.field.key, mod.value, label=mod.field.name)

        elif mod.type == "status":
            if mod.status == "blocked":
                fields = dict(is_blocked=True, is_stopped=False)
                clear_groups = True
            elif mod.status == "stopped":
                fields = dict(is_blocked=False, is_stopped=True)
                clear_groups = True
            else:
                fields = dict(is_blocked=False, is_stopped=False)

        elif mod.type == "groups":
            groups = ContactGroup.user_groups.filter(query=None, uuid__in=[g.uuid for g in mod.groups])
            for group in groups:
                assert not group.is_dynamic, "can't add/remove contacts from dynamic groups"
                if mod.modification == "add":
                    group.contacts.add(*contacts)
                else:
                    group.contacts.remove(*contacts)

        elif mod.type == "urns":
            assert len(contacts) == 1, "should never be trying to bulk update contact URNs"
            assert mod.modification == "set", "should only be setting URNs from here"

            update_urns_locally(contacts[0], mod.urns)

        contacts.update(modified_by=user, modified_on=timezone.now(), **fields)
        if clear_groups:
            for c in contacts:
                Contact.objects.get(id=c.id).clear_all_groups(user)


def update_fields_locally(user, contact, fields):
    for key, val in fields.items():
        update_field_locally(user, contact, key, val)


def update_field_locally(user, contact, key, value, label=None):
    field = ContactField.get_or_create(contact.org, user, key, label=label)

    field_uuid = str(field.uuid)
    if contact.fields is None:
        contact.fields = {}

    if not value:
        value = None
        if field_uuid in contact.fields:
            del contact.fields[field_uuid]

    else:
        field_dict = contact.serialize_field(field, value)

        if contact.fields.get(field_uuid) != field_dict:
            contact.fields[field_uuid] = field_dict

    # update our JSONB on our contact
    with connection.cursor() as cursor:
        if value is None:
            # delete the field
            cursor.execute("UPDATE contacts_contact SET fields = fields - %s WHERE id = %s", [field_uuid, contact.id])
        else:
            # update the field
            cursor.execute(
                "UPDATE contacts_contact SET fields = COALESCE(fields,'{}'::jsonb) || %s::jsonb WHERE id = %s",
                [json.dumps({field_uuid: contact.fields[field_uuid]}), contact.id],
            )


def update_urns_locally(contact, urns: List[str]):
    country = contact.org.get_country_code()
    priority = ContactURN.PRIORITY_HIGHEST

    urns_created = []  # new URNs created
    urns_attached = []  # existing orphan URNs attached
    urns_retained = []  # existing URNs retained

    for urn_as_string in urns:
        normalized = URN.normalize(urn_as_string, country)
        urn = ContactURN.lookup(contact.org, normalized)

        if not urn:
            urn = ContactURN.create(contact.org, contact, normalized, priority=priority)
            urns_created.append(urn)

        # unassigned URN or different contact
        elif not urn.contact or urn.contact != contact:
            urn.contact = contact
            urn.priority = priority
            urn.save()
            urns_attached.append(urn)

        else:
            if urn.priority != priority:
                urn.priority = priority
                urn.save()
            urns_retained.append(urn)

        # step down our priority
        priority -= 1

    # detach any existing URNs that weren't included
    urn_ids = [u.pk for u in (urns_created + urns_attached + urns_retained)]
    urns_detached = ContactURN.objects.filter(contact=contact).exclude(id__in=urn_ids)
    urns_detached.update(contact=None)
