from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
settings_path = ROOT / 'config' / 'settings.yaml'
profile_path = ROOT / 'config' / 'profile.json'

settings = yaml.safe_load(settings_path.read_text(encoding='utf-8')) or {}
settings.setdefault('project', {})['version'] = '0.4.0'
matching = settings.setdefault('matching', {})
matching.setdefault('overall_weights', {'technical': 0.45, 'eligibility': 0.20, 'location': 0.20, 'experience': 0.15})
matching['ranking_caps'] = {
    'non_target_title': 25,
    'international_onsite': 15,
    'remote_geo_incompatible': 30,
    'hard_eligibility': 15,
    'senior_title': 30,
    'three_years': 40,
    'four_plus_years': 25,
    'weak_technical': 30,
}
settings_path.write_text(yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding='utf-8')

profile = json.loads(profile_path.read_text(encoding='utf-8'))
tracker = profile.setdefault('application_tracker', {})
tracker['review_statuses'] = ['unreviewed', 'shortlisted', 'dismissed']
tracker['statuses'] = [
    'not_started', 'application_preparing', 'ready_to_apply', 'applied', 'interview',
    'technical_test', 'hr_interview', 'offer', 'rejected', 'withdrawn', 'expired'
]
profile['language_policy'] = {
    'mandatory_unknown_language': 'exclude',
    'optional_unknown_language': 'keep',
}
profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
print('Configured V0.4 matching caps, workflow statuses and language policy.')