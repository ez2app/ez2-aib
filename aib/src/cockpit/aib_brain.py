#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Local, one-way brain snapshots. No network, Git, scheduler or live restore.

rc 0: complete; 2: rejected; 3: snapshot published with incomplete groups.
Quarantine is metadata only: rejected bytes are never written to a snapshot.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import sys
import tempfile
import unicodedata
import uuid

SCHEMA = 'aib-brain/v1'
VERSION = '4'
SCAN_RULES_VERSION = '4'
REWRITE_FIELDS = {('source', 'vault_root'): 'restored_vault_root'}
MAX_BYTES = 50 * 1024 * 1024
# Both the private exporter and the public runtime import the same table.
_IDENTITY = str(Path(__file__).resolve().parents[1] / 'identity')
if _IDENTITY not in sys.path:
    sys.path.insert(0, _IDENTITY)
from secret_patterns import EXPORT_SECRET_PATTERNS  # noqa: E402
PATTERNS = dict(EXPORT_SECRET_PATTERNS)
PATTERNS.update({
    'github_fine_grained': re.compile(r'github_pat_[A-Za-z0-9_]{20,}'),
    'other_provider_token': re.compile(r'(?:glpat-|hf_|npm_)[A-Za-z0-9_-]{8,}'),
    'bearer': re.compile(r'\bBearer[ \t]+[A-Za-z0-9._~+/=-]{20,}', re.I),
    'basic_authorization': re.compile(r'\bAuthorization[\"\x27]?[ \t]*:[ \t]*[\"\x27]?Basic[ \t]+[A-Za-z0-9+/=]{4,}', re.I),
})
# Normalize snake/kebab/camel credential key components. Values that are only
# token metrics and boolean/null placeholders do not represent credentials.
KEY_WORDS = re.compile(r'(?:密碼|password|passwd|pwd|token|api[ _-]?key|client[ _-]?secret|private[ _-]?key|secret)$', re.I)
ASSIGNMENT = re.compile(
    r"(?<![\w])(?P<key>[A-Za-z0-9_-]*[A-Za-z][A-Za-z0-9_-]*|密碼)"
    r"[\"\x27]?[ \t]*[:=：][ \t]*"
    r"(?:\"(?P<double>(?:\\.|[^\"\\])*)\"|'(?P<single>(?:\\.|[^'\\])*)'|(?P<bare>[^\s,}\]]+))",
    re.I)
# Space-separated API keys are supported without matching arbitrary prose.
SPACED_ASSIGNMENT = re.compile(
    r"(?<![\w])(?P<key>api key|client secret|private key)[\"\x27]?[ \t]*[:=：][ \t]*"
    r"(?:\"(?P<double>(?:\\.|[^\"\\])*)\"|'(?P<single>(?:\\.|[^'\\])*)'|(?P<bare>[^\s,}\]]+))", re.I)


class BrainError(Exception):
    def __init__(self, reason, details=None):
        super().__init__(reason)
        self.details = details or {}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.chmod(path, 0o600)


def validate_document(doc, name):
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / 'src/ledger'))
    from schema_validator import SchemaValidator, SchemaValidationError
    schema = json.loads((root / 'schemas' / (name + '.schema.json')).read_text())
    try:
        SchemaValidator(schema).validate(doc)
    except SchemaValidationError:
        raise BrainError('schema_validation_failed') from None


def save_manifest(path, doc):
    serial = dict(doc)
    serial['files'] = [dict(path=key, **value) for key, value in sorted(doc['files'].items())]
    validate_document(serial, 'brain-manifest')
    if scan(json.dumps(serial, ensure_ascii=False).encode('utf-8')):
        raise BrainError('manifest_contains_credential')
    dump(path, serial)


def inside(path, root):
    return path == root or root in path.parents


def private_parents(path, root):
    if not inside(path, root):
        raise BrainError('private_directory_outside_stage')
    chain = []
    while path != root:
        chain.append(path)
        path = path.parent
    for directory in reversed(chain):
        directory.mkdir(mode=0o700, exist_ok=True)


def safe_rel(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise BrainError('invalid_relative_path')
    p = PurePosixPath(value)
    if p.is_absolute() or any(x in ('..', '.') for x in p.parts) or p.as_posix() != value:
        raise BrainError('invalid_relative_path')
    return p


def open_regular(path):
    # Open each ancestor with NOFOLLOW, not only the final leaf. Directory
    # replacement or a malicious snapshot cannot redirect a read outside roots.
    import stat
    path = Path(path).absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        if not stat.S_ISREG(os.fstat(leaf).st_mode):
            os.close(leaf)
            raise BrainError('not_regular_file')
        return os.fdopen(leaf, 'rb')
    finally:
        os.close(fd)


def read_regular(path):
    with open_regular(path) as stream:
        if os.fstat(stream.fileno()).st_size > MAX_BYTES:
            raise BrainError('too_large')
        data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise BrainError('too_large')
        return data


def stable_read(path, root, attempts=3):
    for _ in range(attempts):
        if not inside(path.resolve(), root):
            raise BrainError('outside_source')
        before = read_regular(path)
        copied = read_regular(path)
        after = read_regular(path)
        if digest(before) != digest(copied) or digest(copied) != digest(after):
            continue
        if len(copied) > MAX_BYTES:
            raise BrainError('too_large')
        text = copied.decode('utf-8')
        try:
            if path.suffix == '.json':
                json.loads(text)
            elif path.suffix == '.jsonl':
                for line in text.splitlines():
                    if line.strip():
                        json.loads(line)
        except ValueError:
            continue
        return copied
    raise BrainError('unstable_or_invalid_json')


def credential_key(key):
    return bool(KEY_WORDS.search(key))


def credential_value(value, key):
    text = value.strip()
    if not text or text.casefold() in ('true', 'false', 'null', 'none'):
        return False
    numeric = re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?%?', text, re.I)
    metric_key = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', key).casefold().replace('-', '_')
    # Numeric passwords/PINs and prefixed access tokens remain sensitive.
    return not (numeric and metric_key in ('token', 'max_token', 'maxtoken'))


def scan_text(text):
    hits = [{'type': kind, 'line': text.count('\n', 0, match.start()) + 1}
            for kind, pattern in PATTERNS.items() for match in pattern.finditer(text)]
    for assignment in (ASSIGNMENT, SPACED_ASSIGNMENT):
        for match in assignment.finditer(text):
            value = next(v for v in match.group('double', 'single', 'bare') if v is not None)
            if credential_key(match['key']) and credential_value(value, match['key']):
                hits.append({'type': 'credential_value', 'line': text.count('\n', 0, match.start()) + 1})
    return hits


def scan(data):
    text = data.decode('utf-8')
    hits = scan_text(text)
    # JSON keys and values may encode even ASCII letters as Unicode escapes.
    # Parse complete JSON or JSONL records rather than treating quotes as syntax
    # that a plain-text expression can skip. Never retain the decoded values.
    def walk(value, raw, first_line):
        if isinstance(value, dict):
            for key, child in value.items():
                token = json.dumps(str(key))
                offset = raw.find(token)
                line = first_line + raw.count('\n', 0, max(0, offset))
                if not isinstance(child, (dict, list)) and child is not None:
                    normalized = json.dumps({key: child}, ensure_ascii=False)
                    for finding in scan_text(normalized):
                        hits.append({'type': finding['type'], 'line': line})
                walk(child, raw, first_line)
        elif isinstance(value, list):
            for child in value:
                walk(child, raw, first_line)
        elif isinstance(value, str):
            for finding in scan_text(value):
                hits.append({'type': finding['type'], 'line': first_line})
    try:
        walk(json.loads(text), text, 1)
    except ValueError:
        for number, line in enumerate(text.splitlines(), 1):
            try:
                walk(json.loads(line), line, number)
            except ValueError:
                pass
    return [dict(type=kind, line=line) for kind, line in sorted({(h['type'], h['line']) for h in hits})]


def excluded(rel):
    parts = rel.parts
    return (any(x in ('.git', '.trash', '.obsidian') for x in parts)
            or parts[:2] == ('.ez2', 'index')
            or any(x == '.env' or x.startswith('.env.') or x.startswith('backup-') for x in parts)
            or any('.bak-' in x for x in parts)
            or rel.suffix.lower() in ('.pem', '.key', '.lock'))


def path_credential(key):
    return bool(scan(key.encode('utf-8')))


def redacted_path(key):
    return 'sha256:' + digest(key.encode('utf-8'))


def candidates(vault, state):
    # Walk without following directory links. All symlinks are excluded,
    # including links within the root, to avoid duplicate/ambiguous ownership.
    for root, prefix in ((vault, 'vault'), (state, 'aib')):
        def walk_error(error):
            raise BrainError('source_inventory_unreadable') from None
        for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
            parent = Path(directory)
            # Early pruning avoids enumerating excluded/unselected subtrees;
            # file-level checks remain the security boundary.
            for name in list(dirs):
                p = parent / name
                rel = p.relative_to(root)
                if path_credential(prefix + '/' + rel.as_posix()) or p.is_symlink() or excluded(rel) or (prefix == 'aib' and len(rel.parts) == 1 and name not in ('projects', 'memory', 'identity')):
                    dirs.remove(name)
                    key = prefix + '/' + rel.as_posix()
                    yield p, root, redacted_path(key) if path_credential(key) else key, 'path_credential' if path_credential(key) else 'excluded'
            for name in sorted(names):
                p = parent / name
                rel = p.relative_to(root)
                reason = None
                if path_credential(prefix + '/' + rel.as_posix()):
                    reason = 'path_credential'
                elif p.is_symlink() or excluded(rel):
                    reason = 'excluded'
                elif prefix == 'aib':
                    selected = ((len(rel.parts) == 3 and rel.parts[0] == 'projects' and name in ('card.json', 'plan.jsonl'))
                                or (len(rel.parts) == 3 and rel.parts[0] == 'memory' and name == 'context.md')
                                or (len(rel.parts) == 2 and rel.parts[0] == 'identity' and name in ('cards.json', 'bindings.json', 'seat-display.json'))
                                or (rel.parts[:2] == ('identity', 'seat-continuity') and p.suffix == '.json'))
                    if not selected:
                        reason = 'not_selected'
                elif p.suffix.lower() != '.md':
                    reason = 'attachment'
                key = prefix + '/' + rel.as_posix()
                yield p, root, redacted_path(key) if reason == 'path_credential' else key, reason


def group(key):
    if key.startswith('aib/projects/'):
        return 'project'
    if key.startswith('aib/memory/'):
        return 'memory'
    if key.startswith(('aib/identity/', 'metadata/')) or key.endswith('/角色.md'):
        return 'persona'
    return 'vault'


@contextmanager
def lock(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = root / '.snapshot.lock'
    fd = os.open(p, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BrainError('snapshot_locked') from None
        yield
    finally:
        os.close(fd)


def load_manifest(snapshot, scan_files=True):
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise BrainError('invalid_snapshot')
    raw_manifest = read_regular(snapshot / 'MANIFEST.json')
    if scan(raw_manifest):
        raise BrainError('manifest_contains_credential')
    doc = json.loads(raw_manifest)
    validate_document(doc, 'brain-manifest')
    if doc.get('schema_version') != SCHEMA or not isinstance(doc.get('files'), list):
        raise BrainError('unsupported_manifest')
    entries = doc['files']
    if len({entry['path'] for entry in entries}) != len(entries):
        raise BrainError('duplicate_manifest_path')
    for g in doc['groups'].values():
        g.setdefault('quarantined', [])
    doc.setdefault('scan_rules_version', '1')
    doc['files'] = {entry['path']: {k: v for k, v in entry.items() if k != 'path'} for entry in entries}
    for key, entry in doc['files'].items():
        rel = safe_rel(key)
        if (rel.parts[0] not in ('vault', 'aib', 'metadata')
                or (rel.parts[0] == 'metadata' and key != 'metadata/route-contract.json') or len(rel.parts) < 2
                or excluded(PurePosixPath(*rel.parts[1:]))
                or (rel.parts[0] == 'vault' and rel.suffix.lower() != '.md')
                or not isinstance(entry.get('sha256'), str)):
            raise BrainError('invalid_manifest_entry')
        path = snapshot / key
        if any(p.is_symlink() for p in [path, *path.parents] if inside(p, snapshot)):
            raise BrainError('snapshot_symlink')
        data = read_regular(path)
        if digest(data) != entry['sha256'] or len(data) != entry['size']:
            raise BrainError('snapshot_hash_mismatch')
        if scan_files and scan(data):
            raise BrainError('snapshot_contains_credential')
    all_paths = list(snapshot.rglob('*'))
    if any(p.is_symlink() for p in all_paths):
        raise BrainError('snapshot_symlink')
    actual = {p.relative_to(snapshot).as_posix() for p in all_paths if p.is_file() and p != snapshot / 'MANIFEST.json'}
    if actual != set(doc['files']):
        raise BrainError('snapshot_inventory_mismatch')
    return doc


def routes():
    route_dir = str(Path(__file__).resolve().parents[1] / 'persona_route')
    if route_dir not in sys.path:
        sys.path.insert(0, route_dir)
    import route_register
    return route_register


def contract_bytes(explicit=None):
    route = routes()
    spec = explicit or route.default_contract_spec()
    if not spec:
        raise BrainError('route_contract_missing')
    if explicit:
        layer = 'explicit'
    elif os.environ.get('EZ2_ROUTE_CONTRACT_SPEC') == spec:
        layer = 'explicit_environment'
    elif os.environ.get('EZ2_ROUTE_CONTRACT_SPEC') and os.environ.get('EZ2_ROUTE_REGISTRY') == spec:
        layer = 'registry_environment'   # 10-15:兩個 env 不同而 REGISTRY 是超集,default_contract_spec 改用它
    elif os.environ.get('EZ2_ROUTE_CONTRACT_SPEC'):
        layer = 'explicit_environment'
    elif spec.startswith('git:'):
        layer = 'git_main'
    elif os.environ.get('EZ2_ROUTE_REGISTRY') == spec:
        layer = 'registry_environment'
    else:
        layer = 'vendor' if spec == getattr(route, 'VENDORED_CONTRACT', None) else 'bundled'
    for _ in range(3):
        raw = route.read_contract_bytes(spec)
        again = route.read_contract_bytes(spec)
        if raw is not None and raw == again:
            if len(raw) > MAX_BYTES or scan(raw):
                raise BrainError('route_contract_quarantined')
            if not isinstance(json.loads(raw), dict):
                raise BrainError('invalid_route_contract')
            return raw, layer
    raise BrainError('route_contract_unreadable_or_unstable')


def select_previous(output, expected_roots):
    candidates = sorted((p for p in output.glob('snap-*') if p.is_dir() and not p.is_symlink()), reverse=True)
    unknown_scope = False
    for candidate in candidates:
        try:
            metadata = json.loads(read_regular(candidate / 'MANIFEST.json'))
            roots = metadata.get('source_roots') if isinstance(metadata, dict) else None
        except (OSError, ValueError, BrainError):
            unknown_scope = True
            continue
        if roots == expected_roots:
            return candidate, 'unreadable_snapshot_scope_skipped' if unknown_scope else None
    return None, 'no_matching_source_roots' if candidates else None


def snapshot_complete(doc):
    return all(g['complete'] for g in doc['groups'].values()) and not doc.get('baseline_warning')


def snapshot(vault, state, output, previous=None, contract=None):
    vault, state, output = [Path(p).expanduser().resolve() for p in (vault, state, output)]
    if not vault.is_dir() or not state.is_dir():
        raise BrainError('source_missing')
    if inside(output, vault) or inside(vault, output) or output == state or inside(state, output):
        raise BrainError('snapshot_source_overlap')
    # A dedicated state/brain subdirectory is supported, never source selections.
    if inside(output, state) and output != state / 'brain':
        raise BrainError('use_dedicated_brain_directory')
    expected_roots = {'vault': str(vault), 'state': str(state)}
    with lock(output):
        baseline_warning = None
        if previous is None:
            previous, baseline_warning = select_previous(output, expected_roots)
        try:
            old = load_manifest(Path(previous).resolve(), scan_files=False) if previous else None
        except BrainError as exc:
            if str(exc) != 'manifest_contains_credential':
                raise
            old = None
            baseline_warning = 'previous_manifest_contains_credential'
        previous_id = old['snapshot_id'] if old else None
        if old and old.get('source_roots') != expected_roots:
            baseline_warning = 'source_roots_mismatch_or_unknown'
            old = None
        ident = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
        temp = Path(tempfile.mkdtemp(prefix='.pending-', dir=output))
        manifest = {'schema_version': SCHEMA, 'snapshot_id': ident, 'tool_version': VERSION,
                    'scan_rules_version': SCAN_RULES_VERSION, 'source_roots': {'vault': str(vault), 'state': str(state)},
                    'host': socket.gethostname(), 'created_at': datetime.now(timezone.utc).isoformat(),
                    'prev_snapshot_id': previous_id, 'baseline_warning': baseline_warning, 'baseline_applied': bool(old), 'previous_rejected': [], 'deleted_ratio': 0, 'files': {}, 'excluded': [], 'quarantined': [],
                    'source_files': [], 'missing_required': [], 'groups': {g: {'complete': True, 'files': 0, 'missing': [], 'quarantined': []} for g in ('persona', 'project', 'memory', 'vault')}}
        try:
            rejected_old = set()
            if old:
                for key in old['files']:
                    hits = scan(read_regular(Path(previous).resolve() / key))
                    if hits:
                        rejected_old.add(key)
                        manifest['previous_rejected'].extend({'path': key, 'kind': h['type'], 'line': h['line']} for h in hits)
            raw_contract, layer = contract_bytes(contract)
            contract_path = 'metadata/route-contract.json'
            (temp / 'metadata').mkdir(mode=0o700)
            (temp / contract_path).write_bytes(raw_contract)
            manifest['contract'] = {'source': layer, 'path': contract_path, 'sha256': digest(raw_contract)}
            manifest['files'][contract_path] = {'sha256': digest(raw_contract), 'size': len(raw_contract), 'group': 'persona', 'current': True}
            manifest['groups']['persona']['files'] += 1
            for path, root, key, reason in candidates(vault, state):
                if reason:
                    entry = {'path': key, 'reason': reason}
                    if reason == 'attachment' and not path.is_symlink():
                        try:
                            h = hashlib.sha256()
                            with open_regular(path) as stream:
                                entry['size'] = os.fstat(stream.fileno()).st_size
                                for block in iter(lambda: stream.read(1024 * 1024), b''):
                                    h.update(block)
                            entry['sha256'] = h.hexdigest()
                        except (OSError, BrainError):
                            entry = {'path': key, 'reason': 'unreadable_attachment'}
                    manifest['excluded'].append(entry)
                    continue
                manifest['source_files'].append(key)
                g = manifest['groups'][group(key)]
                try:
                    data = stable_read(path, root)
                    hits = scan(data)
                except (OSError, UnicodeError, BrainError):
                    data, hits = None, [{'type': 'unreadable_unstable_or_unsupported', 'line': None}]
                if hits:
                    manifest['quarantined'].extend({'path': key, 'kind': h['type'], 'line': h['line'], 'status': 'not_updated'} for h in hits)
                    g['quarantined'].append(key)
                    if old and key in old['files'] and key not in rejected_old:
                        data = read_regular(Path(previous).resolve() / key)
                        # Re-scan with current rules: never inherit newly unsafe bytes.
                        if scan(data):
                            data = None
                    else:
                        data = None
                if data is not None:
                    dst = temp / key
                    private_parents(dst.parent, temp)
                    dst.write_bytes(data)
                    os.chmod(dst, 0o600)
                    manifest['files'][key] = {'sha256': digest(data), 'size': len(data), 'group': group(key), 'current': not bool(hits)}
                    g['files'] += 1
            for required in ('aib/identity/cards.json',):
                if required not in manifest['files']:
                    manifest['groups']['persona']['complete'] = False
                    manifest['groups']['persona']['missing'].append(required)
                    manifest['missing_required'].append(required)
            cards_file = temp / 'aib/identity/cards.json'
            if cards_file.exists():
                cards_doc = json.loads(cards_file.read_bytes())
                if not isinstance(cards_doc.get('cards'), dict):
                    raise BrainError('invalid_cards_document')
                for uid, card in cards_doc['cards'].items():
                    try:
                        rel = safe_rel(card.get('card_path_rel'))
                        key = 'vault/' + str(rel)
                    except (BrainError, AttributeError):
                        key = 'aib/identity/cards.json#' + str(uid)
                    if key not in manifest['files']:
                        manifest['groups']['persona']['complete'] = False
                        manifest['missing_required'].append(key)
                        manifest['groups']['persona']['missing'].append(key)
            projects = state / 'projects'
            if projects.is_dir():
                for directory in projects.iterdir():
                    if directory.is_dir() and not directory.is_symlink():
                        for name in ('card.json', 'plan.jsonl'):
                            key = 'aib/projects/' + directory.name + '/' + name
                            if key not in manifest['files']:
                                key = redacted_path(key) if path_credential(key) else key
                                manifest['groups']['project']['complete'] = False
                                manifest['missing_required'].append(key)
                                manifest['groups']['project']['missing'].append(key)
            memories = state / 'memory'
            if memories.is_dir():
                for directory in memories.iterdir():
                    if directory.is_dir() and not directory.is_symlink():
                        key = 'aib/memory/' + directory.name + '/context.md'
                        if key not in manifest['files']:
                            key = redacted_path(key) if path_credential(key) else key
                            manifest['groups']['memory']['complete'] = False
                            manifest['groups']['memory']['missing'].append(key)
                            manifest['missing_required'].append(key)
            if old:
                baseline = set(old['source_files'])
                missing = baseline - set(manifest['source_files'])
                manifest['deleted_ratio'] = len(missing) / len(baseline) if baseline else 0
                if manifest['deleted_ratio'] > .05:
                    raise BrainError('deletion_threshold_exceeded', {'deleted_ratio': manifest['deleted_ratio'], 'missing': sorted(missing)})
            save_manifest(temp / 'MANIFEST.json', manifest)
            target = output / ('snap-' + ident)
            temp.rename(target)
            return target, manifest
        finally:
            if temp.exists():
                shutil.rmtree(temp)


def rebuild_cards(vault, original, contract_path):
    # Use the canonical card parser and projection builder, never create UIDs.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'memory'))
    import card_registry as registry
    source = original.get('source') or {}
    folder = str(safe_rel(source.get('library_folder')))
    defaults = {uid: c['default_route_key'] for uid, c in original.get('cards', {}).items() if c.get('default_route_key')}
    contract = registry.load_contract(str(contract_path))
    if contract is None:
        raise BrainError('snapshot_route_contract_invalid')
    try:
        result = registry.scan(vault, folder, contract, defaults)
    except registry.CardRegistryError:
        raise BrainError('card_rebuild_rejected') from None
    if result['skipped']:
        raise BrainError('cards_skipped_on_rebuild')
    rebuilt = registry.build_document(result, vault, folder, 0)
    if set(rebuilt['cards']) != set(original.get('cards', {})):
        raise BrainError('card_uid_set_mismatch')
    for uid, card in rebuilt['cards'].items():
        old = original['cards'][uid]
        if card['card_path_rel'] != old.get('card_path_rel') or card['persona_sha'] != old.get('persona_sha'):
            raise BrainError('card_body_relation_mismatch')
    return rebuilt


def check_project_refs(obj, cards):
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in ('persona_uid', 'persona_uid_used') and val and val not in cards:
                raise BrainError('project_persona_reference_missing')
            check_project_refs(val, cards)
    elif isinstance(obj, list):
        for val in obj:
            check_project_refs(val, cards)


def protected_roots(doc):
    if not doc.get('source_roots'):
        raise BrainError('legacy_snapshot_source_roots_missing')
    home = Path.home()
    roots = [home / 'Ez2/Vault', home / '.ez2-aib', home / 'Desktop/Obsidian/Ez2',
             Path(routes().default_vault()), Path(routes().aib_default_vault())]
    roots.extend(Path(os.environ[key]).expanduser() for key in
                 ('EZ2_AIB_HOME', 'EZ2_AIB_STATE_ROOT', 'EZ2_VAULT_DIR', 'EZ2_VAULT_ROOT') if os.environ.get(key))
    roots.extend(Path(value).expanduser() for value in (doc.get('source_roots') or {}).values())
    return {root.resolve() for root in roots}


def has_obsidian_ancestor(target):
    return any((parent / '.obsidian').exists() for parent in (target, *target.parents))


def restore(snapshot_dir, into):
    source = Path(snapshot_dir).expanduser().resolve()
    raw_into = Path(into).expanduser()
    if raw_into.is_symlink():
        raise BrainError('restore_target_symlink')
    target = raw_into.resolve()
    doc = load_manifest(source)
    protected = protected_roots(doc)
    if any(inside(target, p.resolve()) or inside(p.resolve(), target) for p in protected) or inside(target, source) or inside(source, target):
        raise BrainError('restore_target_protected_or_overlap')
    if has_obsidian_ancestor(target):
        raise BrainError('restore_target_in_obsidian_vault')
    if not target.is_dir() or any(target.iterdir()):
        raise BrainError('restore_requires_existing_empty_directory')
    temp = Path(tempfile.mkdtemp(prefix='.brain-restore-', dir=target.parent))
    os.chmod(temp, 0o700)
    report = {'schema': 'aib-brain-restore/v1', 'snapshot_id': doc['snapshot_id'], 'rewrite_rules_version': '1', 'files': {}, 'cards_rebuild': {}, 'warnings': [], 'previous_rejected': doc.get('previous_rejected', []), 'complete': snapshot_complete(doc)}
    if doc.get('baseline_warning'):
        report['warnings'].append({'path': 'MANIFEST.json', 'reason': doc['baseline_warning']})
    report['warnings'].extend({'path': item['path'], 'reason': 'quarantined_' + item['status']} for item in doc['quarantined'])
    report['warnings'].extend({'path': key, 'reason': 'missing_required'} for g in doc['groups'].values() for key in g['missing'])
    try:
        for key, entry in doc['files'].items():
            data = read_regular(source / key)
            if digest(data) != entry['sha256']:
                raise BrainError('snapshot_changed_during_restore')
            rel = safe_rel(key)
            # Identity state remains inert migration material, not live aib state.
            out = key.replace('aib/identity/', 'migration-reference/identity/', 1) if key.startswith('aib/identity/') else key
            dst = temp / out
            private_parents(dst.parent, temp)
            dst.write_bytes(data)
            os.chmod(dst, 0o600)
            copied_hash = digest(read_regular(dst))
            if copied_hash != entry['sha256']:
                raise BrainError('restored_hash_mismatch')
            if ':' in key or unicodedata.normalize('NFC', key) != key:
                report['warnings'].append({'path': key, 'reason': 'filename_portability'})
            report['files'][out] = {'original_sha256': entry['sha256'], 'restored_sha256': copied_hash}
        identity = temp / 'migration-reference' / 'identity'
        if doc['groups']['persona']['complete']:
            old = json.loads((identity / 'cards.json').read_text(encoding='utf-8'))
            contract_info = doc.get('contract') or {}
            contract_path = str(safe_rel(contract_info.get('path')))
            if contract_path != 'metadata/route-contract.json' or contract_info.get('sha256') != doc['files'][contract_path]['sha256']:
                raise BrainError('snapshot_route_contract_mismatch')
            rebuilt = rebuild_cards(temp / 'vault', old, temp / contract_path)
            # Only canonical source.vault_root changes; Markdown is byte-for-byte.
            for keys, rule in REWRITE_FIELDS.items():
                if rule != 'restored_vault_root' or keys != ('source', 'vault_root'):
                    raise BrainError('unsupported_rewrite_rule')
                rebuilt[keys[0]][keys[1]] = str(target / 'vault')
            report['cards_rebuild'] = {'matched': True, 'uids': sorted(rebuilt['cards']), 'body_relations': True, 'project_references': True}
            original_dst = identity / 'cards.original.json'
            original_dst.write_bytes(read_regular(identity / 'cards.json'))
            os.chmod(original_dst, 0o600)
            report['files']['migration-reference/identity/cards.original.json'] = {
                'original_sha256': digest(read_regular(original_dst)), 'restored_sha256': digest(read_regular(original_dst))}
            cards_dst = identity / 'cards.json'
            dump(cards_dst, rebuilt)
            if scan(cards_dst.read_bytes()):
                raise BrainError('rebuilt_index_contains_credential')
            report['files']['migration-reference/identity/cards.json']['restored_sha256'] = digest(cards_dst.read_bytes())
            for path in (temp / 'aib' / 'projects').glob('*/card.json'):
                check_project_refs(json.loads(path.read_text(encoding='utf-8')), rebuilt['cards'])
        else:
            report['cards_rebuild'] = {'matched': False, 'uids': [], 'body_relations': False, 'project_references': False}
            report['warnings'].append({'path': 'aib/identity/cards.json', 'reason': 'rebuild_skipped_missing_required_persona'})
        folded = {}
        for key in doc['files']:
            normalized = unicodedata.normalize('NFC', key).casefold()
            if normalized in folded:
                raise BrainError('filename_case_or_unicode_collision')
            folded[normalized] = key
        bindings = identity / 'bindings.json'
        dump(temp / 'MIGRATION-BINDINGS.json', json.loads(bindings.read_text()) if bindings.exists() else {})
        os.chmod(temp / 'MIGRATION-BINDINGS.json', 0o600)
        validate_document(report, 'brain-restore')
        dump(temp / 'RESTORE-REPORT.json', report)
        # Refuse a concurrently populated target, leave it untouched on failure.
        if any(target.iterdir()):
            raise BrainError('restore_target_changed')
        target.rmdir()
        try:
            temp.rename(target)
        except OSError:
            target.mkdir(exist_ok=True)
            raise
        return report
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='aib brain')
    sub = parser.add_subparsers(dest='command', required=True)
    snap = sub.add_parser('snapshot')
    snap.add_argument('--vault', default=routes().default_vault())
    snap.add_argument('--state-root', default=os.environ.get('EZ2_AIB_STATE_ROOT') or os.environ.get('EZ2_AIB_HOME') or str(Path.home() / '.ez2-aib'))
    snap.add_argument('--output', help='default: <state-root>/brain')
    snap.add_argument('--previous')
    snap.add_argument('--contract', help='explicit route contract; otherwise canonical route_register precedence')
    rest = sub.add_parser('restore')
    rest.add_argument('--snapshot', required=True)
    rest.add_argument('--into', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'snapshot':
            path, doc = snapshot(args.vault, args.state_root, args.output or str(Path(args.state_root) / 'brain'), args.previous, args.contract)
            print(json.dumps({'snapshot': str(path), 'groups': doc['groups'], 'quarantined': len(doc['quarantined']), 'baseline_warning': doc.get('baseline_warning')}, ensure_ascii=False))
            return 0 if snapshot_complete(doc) else 3
        report = restore(args.snapshot, args.into)
        print(json.dumps({'snapshot_id': report['snapshot_id'], 'verified_files': len(report['files'])}))
        return 0 if report['complete'] else 3
    except (BrainError, OSError, ValueError, KeyError, TypeError) as exc:
        # Do not echo arbitrary parser/IO messages containing source bytes.
        reason = str(exc) if isinstance(exc, BrainError) else type(exc).__name__
        print(json.dumps({'ok': False, 'reason': reason, 'details': exc.details if isinstance(exc, BrainError) else {}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
