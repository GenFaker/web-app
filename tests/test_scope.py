"""Coverage boundaries must not turn skipped requirements into fabricated answers."""
from __future__ import annotations

import pytest

from models.domain import Answer, AssessmentScope
from services.evaluator import KnowledgeBase, RuleEvaluator


@pytest.fixture
def kb():
    return KnowledgeBase()


def scoped_answers(kb, scope, **overrides):
    answers = {question.id: Answer.NO for question in kb.scope_questions(scope)}
    answers.update({key: Answer(value) for key, value in overrides.items()})
    return answers


def ids(result):
    return {recommendation.service_id for recommendation in result.recommendations}


def test_full_scope_preserves_legacy_selection_and_questions(kb):
    engine = RuleEvaluator(kb)
    answers = scoped_answers(kb, AssessmentScope(), compute_events='yes')
    legacy = engine.evaluate('Legacy', answers)
    explicit = engine.evaluate('Legacy', answers, AssessmentScope())
    assert legacy.model_dump(exclude={'created_at'}) == explicit.model_dump(exclude={'created_at'})
    assert ids(legacy) == {'lambda', 'iam'}
    assert len(kb.scope_questions()) == 39
    assert len(legacy.assessed_categories) == 5
    assert legacy.excluded_categories == []


def test_partial_assessment_requires_only_selected_category(kb):
    scope = AssessmentScope(mode='partial', categories=['storage'])
    answers = scoped_answers(kb, scope, storage_static='yes', storage_iam='yes')
    result = RuleEvaluator(kb).evaluate('Storage review', answers, scope)
    assert ids(result) == {'s3'}
    assert result.answers == answers
    assert len(result.answers) == 8
    assert result.assessed_categories == ['storage']
    assert set(result.excluded_categories) == {'database', 'compute', 'network', 'security'}
    assert all(risk.source_question_id in answers for rec in result.recommendations for risk in rec.risks)
    assert not result.contradictions
    assert any('Skipped requirements' in limitation for limitation in result.limitations)
    assert any('Dependencies' in limitation for limitation in result.limitations)


def test_partial_compute_does_not_inject_iam_or_security_group_risks(kb):
    scope = AssessmentScope(mode='partial', categories=['compute'])
    result = RuleEvaluator(kb).evaluate('Compute review', scoped_answers(kb, scope, compute_os='yes'), scope)
    assert ids(result) == {'ec2'}
    assert len(result.answers) == 8
    assert not result.contradictions
    assert all(rec.category == 'compute' for rec in result.recommendations)


def test_partial_categories_include_dependencies_only_when_category_in_scope(kb):
    scope = AssessmentScope(mode='partial', categories=['security', 'compute'])
    result = RuleEvaluator(kb).evaluate('Compute + access', scoped_answers(kb, scope, compute_os='yes', security_access='yes'), scope)
    assert ids(result) == {'ec2', 'iam', 'security_groups'}
    assert result.scope.categories == ['compute', 'security']
    assert result.assessed_categories == ['compute', 'security']


def test_partial_security_does_not_invent_conflict_with_skipped_workload(kb):
    scope = AssessmentScope(mode='partial', categories=['security'])
    result = RuleEvaluator(kb).evaluate('Security review', scoped_answers(kb, scope, security_private_ingress='yes', security_access='yes'), scope)
    assert not result.contradictions
    assert any('outside the selected coverage' in value for value in result.unresolved)
    security_group = next(rec for rec in result.recommendations if rec.service_id == 'security_groups')
    assert security_group.conditional


def test_partial_network_does_not_invent_declined_tls_or_iam_answer(kb):
    scope = AssessmentScope(mode='partial', categories=['network'])
    result = RuleEvaluator(kb).evaluate('Delivery review', scoped_answers(kb, scope, network_delivery='yes'), scope)
    assert ids(result) == {'cloudfront'}
    assert result.contradictions == []
    assert 'security_tls' not in result.answers
    assert 'security_access' not in result.answers


def test_full_input_cannot_be_smuggled_into_partial_scope(kb):
    scope = AssessmentScope(mode='partial', categories=['storage'])
    answers = scoped_answers(kb, scope, storage_static='yes')
    with pytest.raises(ValueError, match='Unknown questions: compute_events'):
        RuleEvaluator(kb).evaluate('Storage', {**answers, 'compute_events': 'yes'}, scope)
    del answers['storage_static']
    with pytest.raises(ValueError, match='Missing responses: storage_static'):
        RuleEvaluator(kb).evaluate('Storage', answers, scope)


@pytest.mark.parametrize('service_id', ['s3', 'rds', 'dynamodb', 'ec2', 'lambda', 'route53', 'cloudfront', 'iam', 'acm', 'security_groups'])
def test_single_service_question_set_and_target_are_isolated(kb, service_id):
    scope = AssessmentScope(mode='single', service_id=service_id)
    service = kb.services[service_id]
    questions = kb.scope_questions(scope)
    assert {q.id for q in questions if q.kind == 'requirement'} == set(service['any_yes'])
    assert len([q for q in questions if q.kind == 'control']) == 5
    values = {q.id: Answer.YES for q in questions}
    result = RuleEvaluator(kb).evaluate('Target review', values, scope)
    assert ids(result) == {service_id}
    assert not result.recommendations[0].conditional
    assert len(result.recommendations[0].risks) == 5
    assert result.answers == values
    assert result.assessed_categories == [service['category']]
    assert result.contradictions == []
    assert 'User-requested single-service review' in result.recommendations[0].reason


def test_single_declined_applicability_is_not_claimed_to_be_required(kb):
    scope = AssessmentScope(mode='single', service_id='rds')
    result = RuleEvaluator(kb).evaluate('RDS review', scoped_answers(kb, scope), scope)
    assert ids(result) == {'rds'}
    assert result.recommendations[0].conditional
    assert 'do not establish' in result.recommendations[0].reason
    assert any('not automatically recommended' in value for value in result.unresolved)
    assert result.contradictions == []


def test_single_unknown_controls_and_requirement_remain_unresolved(kb):
    scope = AssessmentScope(mode='single', service_id='lambda')
    values = {q.id: Answer.UNSURE for q in kb.scope_questions(scope)}
    result = RuleEvaluator(kb).evaluate('Uncertain target', values, scope)
    assert ids(result) == {'lambda'}
    assert result.recommendations[0].conditional
    assert all(risk.status == 'unresolved' for risk in result.recommendations[0].risks)
    assert len(result.unresolved) == 6


def test_single_na_control_is_not_waived(kb):
    scope = AssessmentScope(mode='single', service_id='s3')
    result = RuleEvaluator(kb).evaluate('S3 review', scoped_answers(kb, scope, storage_files='yes', storage_iam='not_applicable'), scope)
    assert result.recommendations[0].risks[0].status == 'unresolved'
    assert any('not waived' in value for value in result.unresolved)


@pytest.mark.parametrize('data,match', [
    ({'mode': 'full', 'categories': ['storage']}, 'Full assessment'),
    ({'mode': 'full', 'service_id': 's3'}, 'Full assessment'),
    ({'mode': 'partial'}, 'requires categories'),
    ({'mode': 'partial', 'categories': ['storage'], 'service_id': 's3'}, 'requires categories'),
    ({'mode': 'partial', 'categories': ['storage', 'storage']}, 'unique'),
    ({'mode': 'partial', 'categories': ['unknown']}, 'unknown categories'),
    ({'mode': 'single'}, 'covered service'),
    ({'mode': 'single', 'service_id': 'unknown'}, 'covered service'),
    ({'mode': 'single', 'service_id': 's3', 'categories': ['storage']}, 'covered service'),
])
def test_invalid_scope_combinations_are_rejected(kb, data, match):
    with pytest.raises(ValueError, match=match):
        kb.scope_questions(AssessmentScope(**data))


def test_partial_scope_input_order_is_semantically_repeatable(kb):
    left = AssessmentScope(mode='partial', categories=['storage', 'network'])
    right = AssessmentScope(mode='partial', categories=['network', 'storage'])
    answers = scoped_answers(kb, left, storage_static='yes')
    engine = RuleEvaluator(kb)
    assert engine.evaluate('Ordered review', answers, left).id == engine.evaluate('Ordered review', answers, right).id


def test_scope_changes_assessment_identifier_even_when_question_set_is_equal(kb):
    partial = AssessmentScope(mode='partial', categories=['storage'])
    single = AssessmentScope(mode='single', service_id='s3')
    answers = scoped_answers(kb, partial, storage_static='yes')
    engine = RuleEvaluator(kb)
    assert engine.evaluate('Scope identity', answers, partial).id != engine.evaluate('Scope identity', answers, single).id


def test_public_service_metadata_matches_scoped_requirements(kb):
    services = kb.public_data()['services']
    assert len(services) == 10
    for service in services:
        assert set(service) == {'id', 'name', 'category', 'requirement_ids', 'description', 'guidance'}
        assert service['description']
        assert service['guidance']
        assert all(entry['url'].startswith('https://docs.aws.amazon.com/') for entry in service['guidance'])
        questions = kb.scope_questions(AssessmentScope(mode='single', service_id=service['id']))
        assert service['requirement_ids'] == [q.id for q in questions if q.kind == 'requirement']
