"""A minimal module:callable source adapter used to test plugin resolution."""

from specrouter.adapters.base import SourceResult
from specrouter.models import EndpointRecord
from specrouter.parser import _prime_fields


class FakeAdapter:
    name = "fake"

    def load(self, settings):
        rec = EndpointRecord(
            method="GET", path="/widgets/{id}", operation_id="getWidget",
            summary="Get a widget", description="Fetch a widget by id.",
            tags=["widgets"], parameters=[], tool_name="get_widgets_id",
        )
        _prime_fields(rec)
        return SourceResult(records=[rec], base_url="https://api.example.com", source_hash="fakehash")

    def is_fresh(self, settings, cached):
        return False


def make():
    return FakeAdapter()
