"""Provider diagnostics and saved advisory integration (mock HTTP only)."""
import json
import httpx
import pytest
from fastapi.testclient import TestClient
import app as app_module
from services.llm_client import enrich_report

@pytest.mark.parametrize('provider', ['claude', 'groq'])
@pytest.mark.parametrize('status', [200, 401, 429])
def test_diagnostics_and_saved_advisory(monkeypatch, provider, status):
    async def injected(result, settings):
        explanations = {s.service_id: 'Review the supplied access and encryption controls before deploying this AWS service.' for s in result.recommendations}
        content = json.dumps({'explanations': explanations})
        payload = ({'stop_reason':'end_turn','content':[{'type':'text','text':content}]} if provider == 'claude'
                   else {'choices':[{'finish_reason':'stop','message':{'content':content}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status,json=payload))) as transport:
            return await enrich_report(result, settings, transport)
    monkeypatch.setattr(app_module, 'enrich_report', injected)
    app = app_module.create_app()
    with TestClient(app, base_url='http://127.0.0.1', headers={'X-Advisory-Token':app.state.api_token}) as client:
        settings = {'provider':provider,'api_key':'private-test-key'}
        check = client.post('/api/providers/test',json=settings)
        assert check.status_code == 200
        assert check.json()['ok'] is (status == 200)
        folder = client.post('/api/folders',json={'name':'Provider test'}).json()
        record = client.post('/api/assessments',json={'title':'Test','folder_id':folder['id'],'scope':{'mode':'full'}}).json()
        kb = client.get('/api/knowledge').json()
        answers = {q['id']:'yes' for q in kb['questions']}
        record = client.put('/api/assessments/'+record['id'],json={'title':'Test','answers':answers,'revision':record['revision']}).json()
        response = client.post('/api/evaluate',json={'title':'Test','answers':answers,'scope':record['scope'],'assessment_id':record['id'],'revision':record['revision'],'settings':settings})
        assert response.status_code == 200
        saved = client.get('/api/reports/'+response.json()['handle']).json()['report']
        assert saved['provider_used'] == (provider if status == 200 else 'mock')
        assert len(saved['ai_explanations']) == len(saved['recommendations'])
        assert 'private-test-key' not in json.dumps(saved)
        assert saved['provider_requested'] == provider

def test_provider_diagnostics_requires_session():
    with TestClient(app_module.create_app(),base_url='http://127.0.0.1') as client:
        assert client.post('/api/providers/test',json={'provider':'mock'}).status_code == 403
