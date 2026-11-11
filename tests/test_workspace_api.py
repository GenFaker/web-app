"""Persistence, explicit scope, and concurrent-edit integration coverage."""
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from app import create_app

@pytest.fixture()
def api(tmp_path):
    app=create_app(tmp_path/'durable')
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.headers['X-Advisory-Token']=app.state.api_token
        yield client,app

def create(client,scope=None,title='Customer portal',folder_id=None):
    if folder_id is None:
        folder_id = client.post('/api/folders', json={'name': 'Test folder'}).json()['id']
    response=client.post('/api/assessments',json={'title':title,'scope':scope or {'mode':'full'},'folder_id':folder_id})
    assert response.status_code==201,response.text
    return response.json()

def draft(item,answers=None,**changes):
    payload={'title':item['title'],'answers':answers or {},'notes':'Review encryption before release.','plan_status':'in_progress','revision':item['revision'],'selected_category':None}
    return {**payload,**changes}


@pytest.mark.parametrize('folder_value', [None, '', ' ', 'missing-folder'])
def test_creation_requires_existing_selected_folder(api, folder_value):
    client, _ = api
    payload = {'title': 'Must have a folder', 'scope': {'mode': 'full'}}
    if folder_value is not None:
        payload['folder_id'] = folder_value
    response = client.post('/api/assessments', json=payload)
    assert response.status_code in (404, 422)
    assert client.get('/api/workspace').json()['assessments'] == []

def generate(client,item,answers=None,**changes):
    payload={'assessment_id':item['id'],'revision':item['revision'],'title':item['title'],'scope':item['scope'],'answers':item['answers'] if answers is None else answers,'settings':{'provider':'mock'}}
    return client.post('/api/evaluate',json={**payload,**changes})

def test_folder_and_multiple_drafts_survive_new_application(api,tmp_path):
    client,app=api
    folder=client.post('/api/folders',json={'name':'Website plans'}).json()
    one=create(client,folder_id=folder['id'])
    two=create(client,{'mode':'single','service_id':'rds'},title='Database plan')
    saved=client.put('/api/assessments/'+one['id'],json=draft(one,{'storage_files':'yes'}))
    assert saved.status_code==200
    second_app=create_app(tmp_path/'durable')
    with TestClient(second_app,base_url='http://127.0.0.1',headers={'X-Advisory-Token':second_app.state.api_token}) as reopened:
        workspace=reopened.get('/api/workspace').json()
        assert len(workspace['assessments'])==2
        assert workspace['folders'][0]['assessment_count']==1
        restored=reopened.get('/api/assessments/'+one['id']).json()
        assert restored['answers']=={'storage_files':'yes'}
        assert restored['notes']=='Review encryption before release.'
        assert restored['plan_status']=='in_progress'
        assert reopened.get('/api/assessments/'+two['id']).json()['scope']['mode']=='single'

def test_scoped_saved_report_history_and_exports_are_durable(api,tmp_path):
    client,app=api
    item=create(client,{'mode':'single','service_id':'s3'})
    questions=app.state.knowledge_base.scope_questions(__import__('models.domain',fromlist=['AssessmentScope']).AssessmentScope.model_validate(item['scope']))
    answers={q.id:'yes' for q in questions}
    item=client.put('/api/assessments/'+item['id'],json=draft(item,answers)).json()
    first=generate(client,item)
    assert first.status_code==200,first.text
    result=first.json();item=result['assessment']
    assert len(result['report']['recommendations'])==1
    assert result['report']['recommendations'][0]['service_id']=='s3'
    assert set(result['report']['answers'])==set(answers)
    answers['storage_iam']='no'
    item=client.put('/api/assessments/'+item['id'],json=draft(item,answers)).json()
    second=generate(client,item).json()
    assert len(second['assessment']['reports'])==2
    assert first.json()['handle']!=second['handle']
    app2=create_app(tmp_path/'durable')
    with TestClient(app2,base_url='http://127.0.0.1',headers={'X-Advisory-Token':app2.state.api_token}) as reopened:
        original=reopened.get('/api/reports/'+result['handle']).json()['report']
        assert original['answers']['storage_iam']=='yes'
        current=reopened.get('/api/reports/'+second['handle']).json()['report']
        assert current['answers']['storage_iam']=='no'
        export=reopened.get('/api/reports/'+result['handle']+'/export')
        assert export.status_code==200
        assert 'Amazon S3' in export.text
        assert 'Amazon RDS' not in export.text

def test_partial_scope_normalization_and_missing_or_external_answers(api):
    client,app=api
    item=create(client,{'mode':'partial','categories':['security','storage']})
    assert item['scope']['categories']==['storage','security']
    invalid=client.put('/api/assessments/'+item['id'],json=draft(item,{'database_relational':'yes'}))
    assert invalid.status_code==422
    wrong_tab=client.put('/api/assessments/'+item['id'],json=draft(item,selected_category='database'))
    assert wrong_tab.status_code==422
    assert generate(client,item).status_code==422

@pytest.mark.parametrize('scope',[{'mode':'partial','categories':[]},{'mode':'partial','categories':['bogus']},{'mode':'single','service_id':'unknown'},{'mode':'full','categories':['storage']}])
def test_invalid_scopes_cannot_be_created(api,scope):
    folder = api[0].post('/api/folders',json={'name':'Scope test'}).json()
    assert api[0].post('/api/assessments',json={'title':'Invalid','scope':scope,'folder_id':folder['id']}).status_code==422

def test_conflict_and_unsaved_changes_do_not_overwrite(api):
    client,app=api
    original=create(client)
    saved=client.put('/api/assessments/'+original['id'],json=draft(original,{'storage_files':'yes'})).json()
    stale=client.put('/api/assessments/'+original['id'],json=draft(original,{'storage_files':'no'}))
    assert stale.status_code==409
    assert generate(client,original).status_code==409
    assert generate(client,saved,answers={'storage_files':'no'}).status_code==409
    assert client.get('/api/assessments/'+original['id']).json()['answers']=={'storage_files':'yes'}

def test_provider_settings_are_rejected_by_draft_schema_and_never_saved(api):
    client,app=api
    item=create(client)
    response=client.put('/api/assessments/'+item['id'],json=draft(item,settings={'api_key':'not-for-storage'}))
    assert response.status_code==422
    assert 'not-for-storage' not in response.text
    assert 'api_key' not in client.get('/api/assessments/'+item['id']).text

def test_generation_rejects_changed_scope_before_inference(api):
    client,app=api
    item=create(client,{'mode':'single','service_id':'s3'})
    assert generate(client,item,scope={'mode':'single','service_id':'rds'}).status_code==409
    assert generate(client,item,scope={'mode':'partial','categories':['invalid']}).status_code==422

def test_delete_requires_current_revision_and_removes_saved_item(api):
    client,app=api
    item=create(client)
    assert client.delete('/api/assessments/'+item['id']+'?revision=99').status_code==409
    assert client.delete('/api/assessments/'+item['id']+'?revision=1').status_code==200
    assert client.get('/api/assessments/'+item['id']).status_code==404

def test_edit_during_ai_generation_preserves_newer_draft(api,monkeypatch):
    """Generation must compare revisions again after a delayed provider returns."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import app as module
    from models.domain import AssessmentScope
    client,application=api
    entered,release=Event(),Event()
    async def delayed(result,settings):
        entered.set()
        await asyncio.to_thread(release.wait,5)
        return result
    monkeypatch.setattr(module,'enrich_report',delayed)
    item=create(client,{'mode':'single','service_id':'s3'})
    answers={q.id:'yes' for q in application.state.knowledge_base.scope_questions(AssessmentScope.model_validate(item['scope']))}
    saved=client.put('/api/assessments/'+item['id'],json=draft(item,answers)).json()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(generate,client,saved)
        try:
            assert entered.wait(5),'Provider was not reached'
            updated=client.put('/api/assessments/'+item['id'],json=draft(saved,answers,notes='New plan entered while provider responds'))
            assert updated.status_code==200
        finally:
            release.set()
        assert pending.result(timeout=5).status_code==409
    restored=client.get('/api/assessments/'+item['id']).json()
    assert restored['notes']=='New plan entered while provider responds'
    assert restored['report_count']==0

def test_supported_unicode_planning_note_fits_request_limit(api):
    client,_=api
    item=create(client)
    notes='🌿'*20000
    response=client.put('/api/assessments/'+item['id'],json=draft(item,notes=notes))
    assert response.status_code==200
    assert response.json()['notes']==notes
