"""Customer existence query service."""
from __future__ import annotations
from typing import Protocol

class CustomerLookupRepository(Protocol):
    def customer_exists(self, customer_phone: str) -> bool: ...

class CustomerExists:
    def __init__(self, repository: CustomerLookupRepository) -> None:
        self._repository = repository

    def check(self, customer_phone: str) -> bool:
        return self._repository.customer_exists(customer_phone)
