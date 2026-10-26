import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from chat import help_assistant as help

client = TestClient(help.app)


def test_health_and_unconfigured(monkeypatch):
    monkeypatch.setattr(help, "MODEL", "")
    assert client.get('/health/live').status_code == 200
    result = client.post('/help-api/question', json={'question': '¿Cómo entro?'})
    assert result.status_code == 503
    assert result.headers['cache-control'] == 'no-store'


def test_body_validation():
    for body in ({}, {'question': ''}, {'question': 'x' * 1001},
                 {'question': 'hola', 'messages': []}, ['hola']):
        assert client.post('/help-api/question', json=body).status_code == 400
    assert client.post('/help-api/question', content='hola').status_code == 415
    assert client.post('/help-api/question', content='x' * 8193,
                       headers={'Content-Type': 'application/json'}).status_code == 413


def test_success_failure_busy_and_release(monkeypatch):
    monkeypatch.setattr(help, 'MODEL', 'local-test')
    with patch.object(help, 'generate', return_value='Ve a Mi cuenta.') as generate:
        result = client.post('/help-api/question', json={'question': '  ¿Dónde?  '})
        assert result.json() == {'answer': 'Ve a Mi cuenta.'}
        generate.assert_called_once_with('¿Dónde?')
    with patch.object(help, 'generate', side_effect=RuntimeError('private details')):
        result = client.post('/help-api/question', json={'question': '¿Dónde?'})
        assert result.status_code == 503
        assert 'private' not in result.text
    assert help.GATE.acquire(blocking=False)
    try:
        assert client.post('/help-api/question', json={'question': 'Hola'}).status_code == 429
    finally:
        help.GATE.release()


def test_ollama_receives_only_catalog_and_question(monkeypatch, tmp_path):
    catalog = tmp_path / 'catalog.md'
    catalog.write_text('Catálogo público', encoding='utf-8')
    monkeypatch.setattr(help, 'CATALOG', catalog)
    monkeypatch.setattr(help, 'MODEL', 'local-test')
    with patch.object(help.urllib.request, 'build_opener') as factory:
        factory.return_value.open.return_value.__enter__.return_value.read.return_value = (
            b'{"message":{"content":"Respuesta"}}'
        )
        assert help.generate('Mi pregunta') == 'Respuesta'
        request = factory.return_value.open.call_args.args[0]
        payload = json.loads(request.data)
        assert payload['messages'] == [
            {'role': 'system', 'content': 'Catálogo público'},
            {'role': 'user', 'content': 'Mi pregunta'},
        ]
        assert payload['stream'] is False
        assert 'Authorization' not in request.headers
        assert 'Cookie' not in request.headers


def test_provider_is_local_and_cannot_redirect():
    import pytest
    for url in ('https://example.com', 'http://8.8.8.8', 'http://localhost@evil.com',
                'http://localhost/private', 'http://localhost?secret=1'):
        with pytest.raises(ValueError):
            help.local_endpoint(url)
    assert help.local_endpoint('http://127.0.0.1:11434') == 'http://127.0.0.1:11434/api/chat'
    with pytest.raises(ValueError):
        help.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://example.com')
