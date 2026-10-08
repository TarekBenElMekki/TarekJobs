"""Offline transactional import of immutable canonical Batch001 evidence.

Stores structured source snapshots without coercing historical null provenance.
Existing jobs, generic assessments and manual state are never updated.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3

from sqlalchemy import inspect, or_, select, text
from sqlalchemy.orm import Session

from .database import JobORM, JobSourceORM, get_engine
from .normalize import norm_text
from .production_write_guard import production_write_guard

LOG = logging.getLogger(__name__)
MIGRATION = Path(__file__).resolve().parents[1] / 'migrations/001_ttea_canonical_batches.sql'
REQUIRED = set('external_id source_provider title company location source_url application_url original_model_score category semantic_status semantic_flags flag_classification assessment_version scoring_provider model scored_at strengths gaps hard_requirements reasoning repair_actions repair_error raw_response_reference source_sha256 detail_sha256 profile_sha256 review_status'.split())


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def validate_batch(artifact):
    batch_id = artifact.get('batch_id')
    contracts = {
        'academicpositions_batch001': (95, 5),
        'academicpositions_batch002': (96, 4),
        'academicpositions_batch003': (100, 0),
        'academicpositions_batch004': (100, 0),
    }
    if artifact.get('format_version') != 1 or batch_id not in contracts:
        raise ValueError('Unsupported canonical batch contract')
    records = artifact.get('records', [])
    expected = artifact.get('expected_external_ids', [])
    ids = [r.get('external_id') for r in records]
    if len(records) != 100 or len(set(ids)) != 100 or len(expected) != 100 or set(ids) != set(expected):
        label = {
            'academicpositions_batch001': 'Batch001',
            'academicpositions_batch002': 'Batch002',
            'academicpositions_batch003': 'Batch003',
            'academicpositions_batch004': 'Batch004',
        }[batch_id]
        raise ValueError(f'{label} requires exactly 100 unique inventory IDs')
    expected_m16, expected_calibration = contracts[batch_id]
    m16_count = sum(r.get('partition') == 'm16_certified' for r in records)
    calibration_count = sum(r.get('partition') == 'calibration' for r in records)
    if m16_count != expected_m16 or calibration_count != expected_calibration:
        raise ValueError(
            f'{batch_id} partition must be '
            f'{expected_m16} certified plus '
            f'{expected_calibration} calibration records'
        )
    for r in records:
        if not REQUIRED <= r.keys():
            raise ValueError('Missing canonical evidence fields')
        score = r['original_model_score']
        if type(score) is not int or not 0 <= score <= 100:
            raise ValueError('Invalid immutable model score')
        category = 'reject' if score < 40 else 'maybe' if score < 70 else 'strong'
        if r['category'] != category or r.get('assessment', {}).get('score', score) != score:
            raise ValueError('Score/category conflict; import never repairs scores')
        flags = r['semantic_flags']
        if not isinstance(flags, list) or any(not isinstance(f, str) for f in flags):
            raise ValueError('Invalid semantic flags')
        if r['semantic_status'] != ('flagged' if flags else 'clean') or set(r['flag_classification']) != set(flags):
            raise ValueError('Semantic flags must remain visible and classified')
        if r['partition'] == 'm16_certified' and (r['assessment_version'] != 'ap-full-detail-v2.3.13' or any(r[k] is None for k in ('scoring_provider','model','scored_at','raw_response_reference'))):
            raise ValueError('Missing certified provenance')
        if r['source_provider'] != 'academicpositions.com' or not r['source_url'].startswith('https://academicpositions.com/'):
            raise ValueError('Invalid source identity')
        for field in ('strengths', 'gaps', 'hard_requirements', 'repair_actions'):
            if not isinstance(r[field], list):
                raise ValueError('Structured evidence arrays required')
        ref = r['raw_response_reference']
        if ref is not None:
            raw_bytes = Path(ref['path']).read_bytes()
            if hashlib.sha256(raw_bytes).hexdigest() != ref['sha256']:
                raise ValueError('Raw response hash mismatch')
            raw = json.loads(raw_bytes.decode('utf-8-sig'))
            if 'choices' in raw:
                content = raw['choices'][0]['message']['content']
            else:
                content = ''.join(p.get('text', '') for p in raw['candidates'][0]['content']['parts'])
            if json.loads(content)['score'] != score:
                raise ValueError('Raw numeric score mismatch')
    return records


def _migrate(connection):
    statement = ''
    for line in MIGRATION.read_text(encoding='utf-8-sig').splitlines(True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.exec_driver_sql(statement)
            statement = ''
    if statement.strip():
        raise ValueError('Incomplete migration SQL')


def _insert_record(session, connection, batch_id, r):
    matches = session.scalars(select(JobSourceORM).where(or_(
        JobSourceORM.source_url == r['source_url'],
        (JobSourceORM.source_name == r['source_provider']) & (JobSourceORM.external_id == r['external_id']),
    ))).all()
    job_ids = {s.job_id for s in matches}
    canonical_ids = set(session.scalars(select(JobORM.id).where(JobORM.canonical_url == r['source_url'])))
    if len(job_ids | canonical_ids) > 1:
        raise ValueError('Conflicting strong source identities; refusing merge')
    job_id = next(iter(job_ids | canonical_ids), None)
    new_job = job_id is None
    if new_job:
        # Only documented descriptive facts are supplied. ORM defaults remain
        # legacy workflow defaults, not inferred eligibility or model scores.
        job = JobORM(title=r['title'], company=r['company'], location=r['location'] or '',
                     description=r.get('description') or '', canonical_url=r['source_url'],
                     norm_title=norm_text(r['title']), norm_company=norm_text(r['company']),
                     norm_location=norm_text(r['location'] or ''))
        session.add(job); session.flush(); job_id = job.id
    if not matches:
        session.add(JobSourceORM(job_id=job_id, source_name=r['source_provider'], source_url=r['source_url'], external_id=r['external_id']))
        session.flush()
    connection.execute(text('INSERT INTO ttea_canonical_records (batch_id,external_id,job_id,record_json,record_sha256) VALUES (:batch,:eid,:job,:payload,:hash)'),
        dict(batch=batch_id, eid=r['external_id'], job=job_id, payload=canonical_json(r), hash=digest(r)))
    return new_job


def import_batch(artifact, engine):
    with production_write_guard(engine.url.database, reason="canonical_batch.import"):
        return _import_batch(artifact, engine)


def _import_batch(artifact, engine):
    records = validate_batch(artifact)
    artifact_hash = digest(artifact)
    batch_id = artifact['batch_id']
    LOG.info('Canonical batch import started: batch=%s count=%d', batch_id, len(records))
    with engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        connection.commit()
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        try:
            _migrate(connection)
            existing = connection.execute(text('SELECT artifact_sha256 FROM ttea_canonical_batches WHERE batch_id=:batch'), dict(batch=batch_id)).scalar_one_or_none()
            if existing is not None:
                if existing != artifact_hash:
                    raise ValueError('Existing immutable batch has a different artifact hash')
                actual = connection.execute(text('SELECT external_id,record_sha256 FROM ttea_canonical_records WHERE batch_id=:batch'), dict(batch=batch_id)).all()
                if dict(actual) != {r['external_id']:digest(r) for r in records}:
                    raise ValueError('Existing batch is incomplete or corrupt')
                connection.rollback()
                LOG.info('Canonical batch reused without writes: batch=%s', batch_id)
                return dict(batch_id=batch_id, records_added=0, records_reused=100, new_jobs=0)
            connection.execute(text('INSERT INTO ttea_canonical_batches VALUES (:batch,:hash,:at,100)'),
                dict(batch=batch_id, hash=artifact_hash, at=datetime.now(timezone.utc).isoformat()))
            with Session(bind=connection, join_transaction_mode='rollback_only') as session:
                new_jobs = sum(_insert_record(session, connection, batch_id, r) for r in records)
                session.flush()
                session.commit()
            actual = connection.execute(text('SELECT external_id,record_json FROM ttea_canonical_records WHERE batch_id=:batch'), dict(batch=batch_id)).all()
            if {eid:json.loads(payload) for eid,payload in actual} != {r['external_id']:r for r in records}:
                raise ValueError('Post-write equality audit failed')
            connection.commit()
        except Exception:
            connection.rollback()
            LOG.exception('Canonical import rolled back: batch=%s', batch_id)
            raise
    LOG.info('Canonical batch committed: batch=%s records=100 new_jobs=%d', batch_id, new_jobs)
    return dict(batch_id=batch_id, records_added=100, records_reused=0, new_jobs=new_jobs)


def available(engine):
    return inspect(engine).has_table('ttea_canonical_records')


def query_batch(engine, batch_id, *, action_bucket=None, semantic_status=None, scoring_provider=None, assessment_version=None):
    if not available(engine):
        return []
    filters = {'batch_id':batch_id}
    filters.update({k:v for k,v in dict(action_bucket=action_bucket, semantic_status=semantic_status,
                   scoring_provider=scoring_provider, assessment_version=assessment_version).items() if v is not None})
    clause = ' AND '.join(k+'=:'+k for k in filters)
    with engine.connect() as c:
        rows = c.execute(text('SELECT * FROM ttea_canonical_job_details WHERE '+clause+' ORDER BY external_id'), filters).mappings().all()
    result=[]
    for row in rows:
        item=dict(row);item['record']=json.loads(item.pop('record_json'))
        item['semantic_flags']=item['record']['semantic_flags']
        result.append(item)
    return result


def assessment_for_job(engine, job_id):
    if not available(engine):
        return None
    with engine.connect() as c:
        value = c.execute(text('SELECT record_json FROM ttea_canonical_records WHERE job_id=:id ORDER BY batch_id DESC LIMIT 1'),dict(id=job_id)).scalar_one_or_none()
    return json.loads(value) if value else None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', required=True);parser.add_argument('--db', required=True);parser.add_argument('--log', required=True)
    args=parser.parse_args()
    logging.basicConfig(filename=args.log,level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    artifact=json.loads(Path(args.artifact).read_text(encoding='utf-8-sig'))
    result=import_batch(artifact,get_engine(args.db));print(json.dumps(result))


if __name__ == '__main__':
    main()
