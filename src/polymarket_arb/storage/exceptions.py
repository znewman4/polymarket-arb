"""Storage-layer exceptions."""


class StorageError(RuntimeError):
    """Base class for storage failures."""


class SchemaMismatchError(StorageError):
    """A row failed validation against the pinned pyarrow schema."""
