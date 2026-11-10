"""Tickets as a source of access grants (X5); discovered by assignments.registry."""
from assignments import registry

registry.register_source(
    "tickets.Ticket",
    is_open=lambda t: t.is_open,
    due_at=lambda t: t.resolution_due_at,
    label=lambda t: t.reference,
    url=lambda t: f"/tickets/{t.pk}/",
)
