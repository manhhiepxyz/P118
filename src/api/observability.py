import json
import logging
import re
import uuid
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Dict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Context variable to store correlation ID for the current request
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")

class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware to extract or generate a Correlation ID for every request."""
    
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        corr_id = request.headers.get("X-Correlation-ID")
        if not corr_id:
            corr_id = str(uuid.uuid4())
        
        # Set correlation ID for the current async context
        token = correlation_id_ctx.set(corr_id)
        
        try:
            response = await call_next(request)
            # Inject it into the response headers for the client
            response.headers["X-Correlation-ID"] = corr_id
            return response
        finally:
            correlation_id_ctx.reset(token)

class PIIRedactionJSONFormatter(logging.Formatter):
    """
    JSON Formatter that automatically redacts PII and injects correlation_id.
    """
    
    # Regex patterns for Vietnamese phone numbers, generic emails, and possible IDs
    PII_PATTERNS = [
        # Email
        (re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'), "***@***.com"),
        # Phone (Vietnamese format typically: 09xx, 08xx, 03xx, 849xx etc, 10-11 digits)
        (re.compile(r'(84|0[3|5|7|8|9])+([0-9]{8})\b'), r'\1****\2[-4:]'), # We'll just replace entirely to be safe
        (re.compile(r'\b(0\d{9})\b'), "****xxx"),
        (re.compile(r'\b(\+84\d{9})\b'), "+84****xxx"),
        # Generic CC/ID numbers (12 digits for VN CCCD)
        (re.compile(r'\b(\d{12})\b'), "************"),
    ]

    def format(self, record: logging.LogRecord) -> str:
        # Build the structured log dictionary
        log_record: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": correlation_id_ctx.get(),
            "message": record.getMessage(),
        }

        # Add any extra attributes passed via 'extra'
        if hasattr(record, "props"):
            log_record.update(record.props)
            
        # Exception tracing
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        # Redact PII in the message
        log_record["message"] = self._redact_pii(log_record["message"])

        # Also redact string values in extra props
        for key, value in log_record.items():
            if isinstance(value, str) and key not in ["timestamp", "level", "logger", "correlation_id", "exception"]:
                log_record[key] = self._redact_pii(value)

        return json.dumps(log_record, ensure_ascii=False)

    def _redact_pii(self, text: str) -> str:
        if not text:
            return text
        for pattern, repl in self.PII_PATTERNS:
            text = pattern.sub(repl, text)
        return text

def setup_observability_logging():
    """Configure root logger to use JSON format and stream to stdout."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Remove existing handlers to avoid duplicate logs
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(PIIRedactionJSONFormatter())
    logger.addHandler(stream_handler)
    
    # Avoid verbose output from third-party libs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)

    logger.info("Observability structured JSON logging initialized.")
