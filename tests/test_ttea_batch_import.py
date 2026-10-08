"""Offline tests for immutable, transactional canonical Batch001 imports."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from src import database
from src.database import JobORM, get_engine, init_db
from src import ttea_batch_import as importer


@pytest.fixture
def db(tmp_path):
    engine = get_engine(str(tmp_path/'jobs.db'))
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def artifact(tmp_path):
    rows=[]
    for i in range(100):
        eid='253500' if i==0 else str(260000+i)
        score=[20,45,75][i%3]
        flags=['invalid_gap_basis'] if i==0 else []
        raw=tmp_path/f'{eid}.json'
        raw.write_text(json.dumps({'choices':[{'message':{'content':json.dumps({'score':score})}}]}))
        rows.append(dict(external_id=eid,source_provider='academicpositions.com',title='Research '+eid,company='Example',location='Known location',
            source_url='https://academicpositions.com/ad/example/2026/research/'+eid,application_url=None,description='Source description',
            original_model_score=score,category='reject' if score<40 else 'maybe' if score<70 else 'strong',
            semantic_status='flagged' if flags else 'clean',semantic_flags=flags,flag_classification={f:'model_semantic_error' for f in flags},
            assessment_version='ap-full-detail-v2.3.13' if i<95 else 'ap-full-detail-v2.3.7',
            scoring_provider='cloudflare' if i%2 else 'google_gemini',model='evidenced-model-'+str(i%2),scored_at='2026-09-30T13:00:00+00:00',
            strengths=[],gaps=[{'claim':'Context mismatch','basis':'supported'}] if flags else [],hard_requirements=[],reasoning='Saved reasoning.',
            repair_actions=[],repair_error=None,raw_response_reference={'path':str(raw),'sha256':hashlib.sha256(raw.read_bytes()).hexdigest()},
            source_sha256='a'*64,detail_sha256='b'*64,profile_sha256='c'*64,review_status='unreviewed',
            assessment={'score':score,'category':'reject' if score<40 else 'maybe' if score<70 else 'strong'},
            partition='m16_certified' if i<95 else 'calibration'))
    return dict(format_version=1,batch_id='academicpositions_batch001',expected_external_ids=[r['external_id'] for r in rows],records=rows)


def count(db,table):
    with db.connect() as c:return c.execute(text('SELECT count(*) FROM '+table)).scalar_one()


def test_import_preserves_all_scores_categories_provenance_and_semantics(db,artifact):
    result=importer.import_batch(artifact,db)
    assert result['records_added']==100
    rows=importer.query_batch(db,artifact['batch_id'])
    assert len(rows)==len({r['external_id'] for r in rows})==100
    originals={r['external_id']:r for r in artifact['records']}
    for row in rows:
        assert row['record']==originals[row['external_id']]
    assert [r['external_id'] for r in rows if r['semantic_status']=='flagged']==['253500']
    assert next(r for r in rows if r['external_id']=='253500')['semantic_flags']==['invalid_gap_basis']
    assert sum(r['semantic_status']=='clean' for r in rows)==99


@pytest.mark.parametrize('damage',['missing','duplicate','wrong_score','wrong_category','hidden_flag','invented_provider'])
def test_invalid_batch_rejected_before_writes(db,artifact,damage):
    if damage=='missing':artifact['records'].pop()
    if damage=='duplicate':artifact['records'][-1]=deepcopy(artifact['records'][0])
    if damage=='wrong_score':artifact['records'][0]['original_model_score']=99
    if damage=='wrong_category':artifact['records'][0]['category']='strong'
    if damage=='hidden_flag':artifact['records'][0]['semantic_status']='clean'
    if damage=='invented_provider':artifact['records'][0]['scoring_provider']=None
    with pytest.raises(ValueError):importer.import_batch(artifact,db)
    assert count(db,'jobs')==0


def test_duplicate_is_idempotent_and_does_not_overwrite_manual_or_newer_state(db,artifact):
    importer.import_batch(artifact,db)
    with Session(db) as s,s.begin():
        job=s.scalar(select(JobORM).where(JobORM.title=='Research 253500'))
        job.notes='Manual note';job.review_status='shortlisted';job.application_status='applied';job.overall_score=97
        job.title='Newer manually edited title'
    result=importer.import_batch(artifact,db)
    assert result['records_added']==0 and result['records_reused']==100
    assert count(db,'jobs')==100
    with Session(db) as s:
        job=s.scalar(select(JobORM).where(JobORM.notes=='Manual note'))
        assert (job.title,job.review_status,job.application_status,job.overall_score)==('Newer manually edited title','shortlisted','applied',97)
    changed=deepcopy(artifact);changed['records'][0]['reasoning']='Do not overwrite an existing revision'
    with pytest.raises(ValueError):importer.import_batch(changed,db)


def test_existing_source_job_and_manual_state_are_reused_untouched(db,artifact):
    from src.database import JobSourceORM
    r=artifact['records'][0]
    with Session(db) as s,s.begin():
        job=JobORM(title='Manual title',company='Manual company',location='Manual location',norm_title='manual',norm_company='manual',norm_location='manual',notes='Keep',review_status='dismissed',overall_score=98)
        s.add(job);s.flush();s.add(JobSourceORM(job_id=job.id,source_name=r['source_provider'],source_url=r['source_url'],external_id=r['external_id']))
        job_id=job.id
    importer.import_batch(artifact,db)
    assert count(db,'jobs')==100
    with Session(db) as s:
        job=s.get(JobORM,job_id)
        assert (job.title,job.company,job.location,job.notes,job.review_status,job.overall_score)==('Manual title','Manual company','Manual location','Keep','dismissed',98)


def test_historical_unknown_remains_null(db,artifact):
    row=artifact['records'][-1]
    for field in ('scoring_provider','model','scored_at','assessment_version','raw_response_reference'):
        row[field]=None
    row['provenance_status']='historical_unknown'
    importer.import_batch(artifact,db)
    result=next(r['record'] for r in importer.query_batch(db,artifact['batch_id']) if r['external_id']==row['external_id'])
    assert result==row
    assert result['scored_at'] is None and result['scoring_provider'] is None


def test_mid_import_failure_rolls_back_jobs_records_and_schema(db,artifact,monkeypatch):
    original=importer._insert_record
    calls=[]
    def broken(*args,**kwargs):
        calls.append(1)
        if len(calls)==3:raise RuntimeError('Injected failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(importer,'_insert_record',broken)
    with pytest.raises(RuntimeError,match='Injected failure'):importer.import_batch(artifact,db)
    assert count(db,'jobs')==0 and count(db,'job_sources')==0
    assert 'ttea_canonical_records' not in inspect(db).get_table_names()


@pytest.mark.parametrize('filters',[{'action_bucket':'apply'},{'action_bucket':'review'},{'action_bucket':'reject'},{'semantic_status':'flagged'},{'scoring_provider':'cloudflare'},{'assessment_version':'ap-full-detail-v2.3.13'}])
def test_query_filters(db,artifact,filters):
    importer.import_batch(artifact,db)
    rows=importer.query_batch(db,artifact['batch_id'],**filters)
    assert rows
    for row in rows:
        assert all(row[k]==v for k,v in filters.items())


def test_api_detail_exposes_full_assessment(db,artifact,monkeypatch):
    importer.import_batch(artifact,db)
    # Canonical evidence does not admit a job to the mandatory active pool.
    with db.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE ttea_job_decisions (canonical_job_id INTEGER PRIMARY KEY, gate_decision TEXT NOT NULL, score INTEGER)")
        connection.exec_driver_sql("INSERT INTO ttea_job_decisions SELECT id, 'KEEP', 20 FROM jobs")
    monkeypatch.setattr(database,'get_engine',lambda _:db)
    name='src._batch_ui_test'
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[1]/'src/ui_server.py')
    module=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,name,module);spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        rows=client.get('/api/ttea/batches/academicpositions_batch001/jobs?semantic_status=flagged').json()['items']
        assert len(rows)==1
        payload=client.get('/api/jobs/'+str(rows[0]['job_id'])).json()
        semantic=payload['canonical_assessment']
        for field in ('original_model_score','category','semantic_status','scoring_provider','model','strengths','gaps','hard_requirements','reasoning','semantic_flags'):
            assert field in semantic
        assert semantic['semantic_flags']==['invalid_gap_basis']
        assert payload['score']['value']==20


# BATCH004_CANONICAL_CONTRACT_TEST_V1
def test_batch004_accepts_100_certified_records_and_is_idempotent(
    db,
    artifact,
):
    from copy import deepcopy

    batch = deepcopy(artifact)

    batch['batch_id'] = (
        'academicpositions_batch004'
    )

    for row in batch['records']:
        row['partition'] = 'm16_certified'
        row['assessment_version'] = (
            'ap-full-detail-v2.3.13'
        )
        row['scoring_provider'] = (
            'google_gemini'
        )
        row['model'] = (
            'gemini-3.5-flash-lite'
        )
        row['scored_at'] = (
            '2026-10-01T00:00:00+00:00'
        )
        row['provenance_status'] = (
            'complete'
        )

    validated = importer.validate_batch(
        batch
    )

    assert len(validated) == 100

    assert {
        row['partition']
        for row in validated
    } == {
        'm16_certified'
    }

    first = importer.import_batch(
        batch,
        db,
    )

    assert first['records_added'] == 100
    assert first['records_reused'] == 0

    queried = importer.query_batch(
        db,
        'academicpositions_batch004',
    )

    assert len(queried) == 100

    assert {
        row['record']['external_id']
        for row in queried
    } == set(
        batch['expected_external_ids']
    )

    second = importer.import_batch(
        batch,
        db,
    )

    assert second == {
        'batch_id':
            'academicpositions_batch004',

        'records_added':
            0,

        'records_reused':
            100,

        'new_jobs':
            0,
    }

    damaged = deepcopy(
        batch
    )

    damaged['records'][0][
        'partition'
    ] = 'calibration'

    with pytest.raises(
        ValueError,
        match='partition must be',
    ):
        importer.validate_batch(
            damaged
        )
