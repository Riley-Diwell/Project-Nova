"""Data structures for the Persona store.

A Persona holds the durable, slow-changing model of *who the user is*
(opinions -> likes -> food -> [bagels]). Vector-searchable. NO raw sensor data.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class Fact(BaseModel):
    """One durable belief about the user.

    `text` is the grounded statement (indexicals already dereferenced before
    upsert, for grounding. `category` is the hierarchy path into the ontology, 
    e.g. ["opinions", "likes", "food"].
    """

    text: str
    category: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Set by the store; do not populate by hand.
    id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class Match(BaseModel):
    """A Fact returned from semantic search, with its similarity score."""

    fact: Fact
    similarity: float  # cosine similarity in [0, 1]; higher is closer


class PersonaQuery(BaseModel):
    """Read filter for `persona.search`."""

    text: str                          # natural-language query, embedded then matched
    category: Optional[list[str]] = None  # restrict to a subtree of the ontology
    limit: int = 5
    min_similarity: float = 0.0        # drop weak matches below this score
