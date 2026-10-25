"""Transport-level performance helpers: selective gzip and long-lived asset caching."""
from starlette.middleware.gzip import GZipMiddleware
from starlette.staticfiles import StaticFiles

# Server-sent-event streams must flush each chunk immediately; gzip would buffer them.
_STREAM_SUFFIXES = ("/events", "/stream")


class SelectiveGZipMiddleware(GZipMiddleware):
    """GZip for JSON/JS/CSS responses, but never for SSE endpoints."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].endswith(_STREAM_SUFFIXES):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


class ImmutableStaticFiles(StaticFiles):
    """Vite emits content-hashed filenames under /assets, so browsers can cache them forever."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
