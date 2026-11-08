"""Structured HTTP-compatible errors shared by provider request validation."""

from fastapi import HTTPException


class ProviderPayloadTooLarge(HTTPException):
    def __init__(self, detail: dict):
        super().__init__(status_code=413, detail=detail)


class ProviderCapabilityUnsupported(HTTPException):
    def __init__(self, detail: dict):
        super().__init__(status_code=400, detail=detail)
