"""Reviewable scenario benchmarks for deterministic draft rules, not expert labels."""
import json
from pathlib import Path

import pytest

from models.domain import Answer
from services.evaluator import KnowledgeBase, RuleEvaluator


@pytest.fixture
def kb():
    return KnowledgeBase()


@pytest.fixture
def evaluator(kb):
    return RuleEvaluator(kb)


def responses(kb, **overrides):
    """Use explicit No responses so test cases never depend on hidden defaults."""
    result = {question.id: Answer.NO for question in kb.questions}
    result.update({key: Answer(value) for key, value in overrides.items()})
    return result


def service_ids(result, conditional=None):
    return {rec.service_id for rec in result.recommendations
            if conditional is None or rec.conditional == conditional}


def test_matrix_has_five_categories_and_all_risk_dimensions(kb):
    assert len(kb.questions) == 39
    assert len(kb.services) == 10
    assert sum(len(service['risks']) for service in kb.services.values()) == 50
    assert 'expert validation required' in kb.review_status
    for category in kb.categories:
        controls = [q for q in kb.questions if q.category == category['id'] and q.kind == 'control']
        assert len(controls) == 5
        assert {q.dimension for q in controls} == set(kb.DIMENSIONS)


def test_all_absent_requirements_do_not_invent_services(kb, evaluator):
    result = evaluator.evaluate('No requirements yet', responses(kb))
    assert not result.recommendations
    assert 'No covered AWS service requirement' in result.summary


@pytest.mark.parametrize('requirements, expected', [
    ({'storage_static': 'yes', 'network_domain': 'yes', 'network_delivery': 'yes', 'security_tls': 'yes'},
     {'s3', 'route53', 'cloudfront', 'iam', 'acm'}),
    ({'compute_events': 'yes', 'database_keyvalue': 'yes'}, {'lambda', 'dynamodb', 'iam'}),
    ({'compute_os': 'yes', 'database_relational': 'yes'}, {'ec2', 'rds', 'iam', 'security_groups'}),
    ({'compute_long_running': 'yes'}, {'ec2', 'iam', 'security_groups'}),
    ({'storage_uploads': 'yes'}, {'s3', 'iam'}),
    ({'network_global_delivery': 'yes'}, {'cloudfront', 'iam'}),
])
def test_expected_service_selection_scenarios(kb, evaluator, requirements, expected):
    result = evaluator.evaluate('Scenario', responses(kb, **requirements))
    assert service_ids(result) == expected
    assert service_ids(result, conditional=True) == set()


def test_hybrid_workloads_can_select_both_alternatives(kb, evaluator):
    result = evaluator.evaluate('Hybrid', responses(kb, database_relational='yes', database_keyvalue='yes', compute_os='yes', compute_events='yes'))
    assert {'rds', 'dynamodb', 'ec2', 'lambda'} <= service_ids(result, conditional=False)


def test_unknown_requirements_remain_conditional(kb, evaluator):
    result = evaluator.evaluate('Unresolved database', responses(kb, database_relational='not_sure'))
    assert service_ids(result, conditional=False) == set()
    assert service_ids(result, conditional=True) == {'rds', 'iam', 'security_groups'}
    assert any('requirement unresolved' in value for value in result.unresolved)


def test_unknown_control_is_not_secure_or_insecure(kb, evaluator):
    result = evaluator.evaluate('Unknown control', responses(kb, storage_static='yes', storage_data='not_sure'))
    storage = next(r for r in result.recommendations if r.service_id == 's3')
    assert next(r for r in storage.risks if r.dimension == 'Data Security').status == 'unresolved'
    assert any('Data Security' in value for value in result.unresolved)


def test_na_does_not_suppress_applicable_controls(kb, evaluator):
    result = evaluator.evaluate('N/A controls', responses(kb, storage_files='yes', storage_iam='not_applicable'))
    storage = next(r for r in result.recommendations if r.service_id == 's3')
    assert next(r for r in storage.risks if r.dimension == 'Identity and Access Management').status == 'unresolved'
    assert any('not waived' in value for value in result.unresolved)


def test_self_reported_yes_is_planned_not_verified(kb, evaluator):
    result = evaluator.evaluate('Planned controls', responses(kb, compute_events='yes', compute_iam='yes'))
    function = next(r for r in result.recommendations if r.service_id == 'lambda')
    assert function.risks[0].status == 'planned'
    assert any('has not been verified' in text for text in result.limitations)


def test_contradictions_are_reported_without_invented_service(kb, evaluator):
    result = evaluator.evaluate('Conflicting responses', responses(kb, storage_iam='yes', network_delivery='yes', security_tls='no'))
    assert 's3' not in service_ids(result)
    assert any('Storage controls' in text for text in result.contradictions)
    assert any('TLS plan' in text for text in result.contradictions)


def test_private_boundary_without_workload_requires_clarification(kb, evaluator):
    result = evaluator.evaluate('Private boundary only', responses(kb, security_private_ingress='yes'))
    assert service_ids(result, conditional=False) == set()
    assert service_ids(result, conditional=True) == {'security_groups', 'iam'}
    assert any('VPC-attached resource' in value for value in result.contradictions)


def test_vpc_lambda_gets_security_group_only_when_requested(kb, evaluator):
    plain = evaluator.evaluate('Plain function', responses(kb, compute_events='yes'))
    private = evaluator.evaluate('VPC function', responses(kb, compute_events='yes', security_private_ingress='yes'))
    assert 'security_groups' not in service_ids(plain)
    assert 'security_groups' in service_ids(private, conditional=False)


def test_service_selection_is_independent_of_control_answers(kb, evaluator):
    first = responses(kb, storage_static='yes', database_relational='yes')
    second = dict(first)
    for q in kb.questions:
        if q.kind == 'control':
            second[q.id] = Answer.YES
    assert service_ids(evaluator.evaluate('A', first)) == service_ids(evaluator.evaluate('B', second))


def test_risks_include_curated_actions_and_three_frameworks(kb, evaluator):
    result = evaluator.evaluate('Full workload', {q.id: Answer.YES for q in kb.questions})
    assert len(result.recommendations) == 10
    for service in result.recommendations:
        for risk in service.risks:
            assert risk.action
            assert risk.source_question_id in result.answers
            assert risk.aws_doc_url.startswith('https://docs.aws.amazon.com/')
            assert len(risk.frameworks) == 3
            assert risk.status == 'planned'


def test_evaluation_identifier_and_decisions_are_repeatable(kb, evaluator):
    values = responses(kb, compute_os='yes', database_keyvalue='yes')
    first = evaluator.evaluate('Repeated input', values)
    second = evaluator.evaluate('Repeated input', dict(reversed(list(values.items()))))
    assert first.id == second.id
    assert first.model_dump(exclude={'created_at'}) == second.model_dump(exclude={'created_at'})
    assert evaluator.evaluate('Different project', values).id != first.id


def test_missing_answers_fail_closed(kb, evaluator):
    values = responses(kb)
    del values['storage_static']
    with pytest.raises(ValueError, match='Missing responses: storage_static'):
        evaluator.evaluate('Incomplete', values)


def test_unknown_question_and_invalid_answer_fail_closed(kb, evaluator):
    with pytest.raises(ValueError, match='Unknown questions'):
        evaluator.evaluate('Unknown question', {**responses(kb), 'invented': 'yes'})
    with pytest.raises(ValueError, match='Invalid response'):
        evaluator.evaluate('Invalid answer', {**responses(kb), 'storage_static': 'maybe'})


@pytest.mark.parametrize('title', ['', '  ', 'x' * 101])
def test_invalid_titles_are_rejected(kb, evaluator, title):
    with pytest.raises(ValueError, match='title'):
        evaluator.evaluate(title, responses(kb))


def test_malformed_kb_without_dimension_is_rejected(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'data' / 'knowledge_base.json'
    data = json.loads(source.read_text())
    data['services']['s3']['risks'].pop()
    invalid = tmp_path / 'bad.json'
    invalid.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='Incomplete risk matrix'):
        KnowledgeBase(invalid)


def test_confirmed_service_requires_confirmed_iam_despite_unknown_identity_answer(kb, evaluator):
    result = evaluator.evaluate('Identity dependency', responses(kb, storage_static='yes', security_access='not_sure'))
    assert service_ids(result, conditional=False) == {'s3', 'iam'}
    assert any('requirement unresolved' in value for value in result.unresolved)


def test_legitimate_hybrid_workloads_are_not_reported_as_contradictory(kb, evaluator):
    result = evaluator.evaluate('Deliberate hybrid workload', responses(
        kb, database_relational='yes', database_keyvalue='yes',
        compute_os='yes', compute_events='yes', security_access='yes',
    ))
    assert {'rds', 'dynamodb', 'ec2', 'lambda'} <= service_ids(result, conditional=False)
    assert result.contradictions == []


def test_transactions_alone_are_not_used_to_distinguish_database_models(kb):
    relational = next(q for q in kb.questions if q.id == 'database_relational')
    assert 'SQL queries' in relational.text
    assert 'transactions' not in relational.text.lower()
    assert 'DynamoDB' in relational.help


def test_no_control_response_does_not_become_planned_or_secure(kb, evaluator):
    result = evaluator.evaluate('No controls planned', responses(kb, storage_files='yes', security_access='yes'))
    assert all(risk.status == 'gap' for service in result.recommendations for risk in service.risks)
    assert all('verified' not in service.reason.lower() for service in result.recommendations)


def test_unknown_only_workload_does_not_become_confirmed(kb, evaluator):
    values = {q.id: Answer.UNSURE for q in kb.questions}
    result = evaluator.evaluate('All requirements unknown', values)
    assert len(result.recommendations) == 10
    assert all(service.conditional for service in result.recommendations)
    assert all(risk.status == 'unresolved' for service in result.recommendations for risk in service.risks)


def test_declining_identity_access_cannot_waive_iam_dependency(kb, evaluator):
    result = evaluator.evaluate('Identity not applicable', responses(kb, compute_events='yes', security_access='not_applicable'))
    assert 'iam' in service_ids(result, conditional=False)
    assert any('IAM remains a service dependency' in value for value in result.contradictions)
