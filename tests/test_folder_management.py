"""Folder rename/removal and record moves preserve assessment history."""
import pytest
from fastapi.testclient import TestClient
from app import create_app

@pytest.fixture()
def client(tmp_path):
    app = create_app(tmp_path / 'web')
    with TestClient(app, base_url='http://127.0.0.1', headers={'X-Advisory-Token':app.state.api_token}) as client:
        yield client

def folder(client, name):
    response = client.post('/api/folders', json={'name':name})
    assert response.status_code == 201
    return response.json()

def assessment(client, folder):
    return client.post('/api/assessments', json={'title':'Security review','folder_id':folder['id'],'scope':{'mode':'single','service_id':'s3'}}).json()

def test_rename_requires_current_revision_and_valid_name(client):
    item = folder(client, 'Before')
    url = '/api/folders/' + item['id']
    assert client.patch(url, json={'name':' ', 'revision':1}).status_code == 422
    renamed = client.patch(url, json={'name':'After', 'revision':1})
    assert renamed.status_code == 200
    assert renamed.json()['name'] == 'After'
    assert renamed.json()['revision'] == 2
    assert client.patch(url, json={'name':'Stale', 'revision':1}).status_code == 409
    assert client.request('DELETE', url, json={'revision':1}).status_code == 409

def test_empty_folder_deletion(client):
    item = folder(client, 'Empty')
    assert client.request('DELETE','/api/folders/'+item['id'],json={'revision':1}).status_code == 200
    assert client.get('/api/workspace').json()['folders'] == []

def test_nonempty_delete_requires_destination_and_preserves_reports(client):
    source, target = folder(client, 'Source'), folder(client, 'Target')
    record = assessment(client, source)
    knowledge = client.get('/api/knowledge').json()
    answers = {q['id']:'yes' for q in knowledge['questions'] if q['category'] == 'storage'}
    record = client.put('/api/assessments/'+record['id'], json={'title':record['title'],'answers':answers,'revision':record['revision']}).json()
    response = client.post('/api/evaluate', json={'assessment_id':record['id'],'revision':record['revision'],'title':record['title'],'answers':answers,'scope':record['scope']})
    assert response.status_code == 200, response.text
    report = response.json()
    handle = report['handle']
    original = client.get('/api/assessments/'+record['id']).json()
    url = '/api/folders/'+source['id']
    assert client.request('DELETE',url,json={'revision':1}).status_code == 409
    assert client.request('DELETE',url,json={'revision':1,'destination_id':source['id']}).status_code == 409
    assert client.request('DELETE',url,json={'revision':1,'destination_id':'missing'}).status_code == 404
    response = client.request('DELETE',url,json={'revision':1,'destination_id':target['id']})
    assert response.status_code == 200
    assert response.json()['moved'] == 1
    moved = client.get('/api/assessments/'+record['id']).json()
    assert moved['folder_id'] == target['id']
    assert moved['revision'] == original['revision'] + 1
    for key in ('answers','notes','reports','latest_report'):
        assert moved[key] == original[key]
    assert client.get('/api/reports/'+handle).status_code == 200
    folders = client.get('/api/workspace').json()['folders']
    assert len(folders) == 1 and folders[0]['assessment_count'] == 1

def test_move_rejects_stale_revision_and_missing_destination(client):
    source, target = folder(client, 'Source'), folder(client, 'Target')
    record = assessment(client, source)
    url = '/api/assessments/'+record['id']+'/folder'
    assert client.patch(url,json={'folder_id':'missing','revision':1}).status_code == 404
    response = client.patch(url,json={'folder_id':target['id'],'revision':1})
    assert response.status_code == 200
    assert response.json()['folder_id'] == target['id']
    assert client.patch(url,json={'folder_id':source['id'],'revision':1}).status_code == 409

def test_web_data_is_isolated_from_desktop(monkeypatch, tmp_path):
    monkeypatch.setenv('AEGIS_DATA_DIR',str(tmp_path/'desktop'))
    monkeypatch.setenv('AEGIS_WEB_DATA_DIR',str(tmp_path/'web'))
    app = create_app()
    assert app.state.workspace.directory == (tmp_path/'web').resolve()
    assert not (tmp_path/'desktop').exists()
