"""Host normalization, error text, model-name matching, and embeddings route fallback."""
import unittest

import httpx

from app.services import embeddings, llm_assist


class BaseUrlTests(unittest.TestCase):
    def test_bare_host_gets_default_ollama_port(self):
        self.assertEqual(llm_assist._base_url("192.168.1.5"), "http://192.168.1.5:11434")
        self.assertEqual(llm_assist._base_url("ollama"), "http://ollama:11434")

    def test_explicit_port_and_scheme_untouched(self):
        self.assertEqual(llm_assist._base_url("192.168.1.5:11434"), "http://192.168.1.5:11434")
        self.assertEqual(llm_assist._base_url("https://llm.example.com"), "https://llm.example.com")
        self.assertEqual(llm_assist._base_url("http://h:8080/"), "http://h:8080")

    def test_pasted_endpoint_cut_back_to_origin(self):
        self.assertEqual(llm_assist._base_url("http://h:11434/v1"), "http://h:11434")
        self.assertEqual(llm_assist._base_url("http://h:11434/api/tags"), "http://h:11434")
        self.assertEqual(llm_assist._base_url("h:1234/v1/"), "http://h:1234")

    def test_empty(self):
        self.assertEqual(llm_assist._base_url("  "), "")


class ErrorTextTests(unittest.TestCase):
    def test_ollama_error_body_surfaced(self):
        req = httpx.Request("POST", "http://h/api/generate")
        resp = httpx.Response(404, json={"error": "model 'x' not found"}, request=req)
        err = httpx.HTTPStatusError("nope", request=req, response=resp)
        self.assertEqual(llm_assist.describe_error(err), "HTTP 404: model 'x' not found")

    def test_empty_exception_falls_back_to_class_name(self):
        self.assertEqual(llm_assist.describe_error(httpx.ReadTimeout("")), "ReadTimeout")


class ModelMatchTests(unittest.TestCase):
    def test_latest_tag_is_implicit(self):
        self.assertTrue(llm_assist._model_matches("llama3", ["llama3:latest"]))
        self.assertTrue(llm_assist._model_matches("llama3:latest", ["llama3"]))
        self.assertFalse(llm_assist._model_matches("llama3:8b", ["llama3:latest"]))


class EmbedFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_route_then_legacy_fallback(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/api/embed":
                return httpx.Response(404, text="404 page not found")
            return httpx.Response(200, json={"embedding": [0.1, 0.2]})

        real = httpx.AsyncClient
        embeddings.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
        try:
            vec = await embeddings.embed("h", "all-minilm", "hello")
        finally:
            embeddings.httpx.AsyncClient = real
        self.assertEqual(vec, [0.1, 0.2])
        self.assertEqual(calls, ["/api/embed", "/api/embeddings"])


if __name__ == "__main__":
    unittest.main()
