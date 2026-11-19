"""Local, transactional review history and model registry. No pickle or cloud service."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid
import zlib

import numpy as np


def json_value(value):
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def dumps(value):
    return json.dumps(json_value(value), sort_keys=True, allow_nan=False)


def fingerprint(*values):
    digest = hashlib.sha256()
    for value in values:
        part = value if isinstance(value, bytes) else dumps(value).encode()
        digest.update(len(part).to_bytes(8, 'big'))
        digest.update(part)
    return digest.hexdigest()


def image_identity(image):
    image = np.asarray(image)
    return fingerprint(image.shape, image.dtype.str, np.ascontiguousarray(image).tobytes())


def pack_mask(mask):
    return zlib.compress(np.packbits(np.asarray(mask, bool).ravel()).tobytes())


def unpack_mask(blob, bbox):
    y0, x0, y1, x1 = bbox
    count = (y1 - y0) * (x1 - x0)
    raw = zlib.decompress(blob)
    if len(raw) != (count + 7) // 8:
        raise ValueError('Stored object mask has an invalid size.')
    return np.unpackbits(np.frombuffer(raw, np.uint8), count=count).reshape(y1-y0, x1-x0).astype(bool)


def feedback_directory():
    if os.environ.get('COLLAGEN_FEEDBACK_DIR'):
        return Path(os.environ['COLLAGEN_FEEDBACK_DIR']).expanduser()
    base = (Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
            if os.name == 'nt' else Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')))
    return base / 'CollagenFiberAnalyzer' / 'feedback'


SCHEMA = '''
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO meta VALUES ('schema_version', 1), ('revision', 0);
CREATE TABLE IF NOT EXISTS images (
 image_key TEXT PRIMARY KEY, image_name TEXT NOT NULL, pixels BLOB NOT NULL,
 role TEXT CHECK(role IN ('training', 'evaluation')), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS image_sources (
 image_key TEXT NOT NULL REFERENCES images, image_name TEXT NOT NULL, source_sha TEXT NOT NULL,
 PRIMARY KEY (image_key, image_name, source_sha));
CREATE TABLE IF NOT EXISTS analyses (
 analysis_key TEXT PRIMARY KEY, image_key TEXT NOT NULL REFERENCES images,
 parameters TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS objects (
 object_key TEXT PRIMARY KEY, mask_key TEXT NOT NULL, analysis_key TEXT NOT NULL REFERENCES analyses,
 fiber_id INTEGER NOT NULL, kind TEXT NOT NULL, original_classification TEXT NOT NULL,
 original_model_id TEXT, bbox TEXT NOT NULL, mask BLOB NOT NULL, features TEXT NOT NULL,
 measurement TEXT NOT NULL, manual_classification TEXT CHECK(manual_classification IN ('accepted','rejected')),
 active INTEGER NOT NULL DEFAULT 1, state_event INTEGER,
 UNIQUE(analysis_key, fiber_id));
CREATE TABLE IF NOT EXISTS events (
 event_id INTEGER PRIMARY KEY, analysis_key TEXT NOT NULL REFERENCES analyses,
 object_key TEXT NOT NULL REFERENCES objects, action TEXT NOT NULL,
 before_classification TEXT, after_classification TEXT, before_active INTEGER NOT NULL,
 after_active INTEGER NOT NULL, automatic_classification TEXT NOT NULL, model_id TEXT,
 undo_target INTEGER UNIQUE REFERENCES events, event_token TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 selection_context TEXT);
CREATE TABLE IF NOT EXISTS models (
 model_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL,
 report TEXT NOT NULL, approved INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS model_samples (
 model_id TEXT NOT NULL REFERENCES models, object_key TEXT NOT NULL REFERENCES objects,
 event_id INTEGER NOT NULL REFERENCES events, image_key TEXT NOT NULL REFERENCES images,
 label TEXT NOT NULL, role TEXT NOT NULL, features TEXT NOT NULL,
 PRIMARY KEY(model_id, object_key));
CREATE INDEX IF NOT EXISTS objects_analysis ON objects(analysis_key);
CREATE INDEX IF NOT EXISTS events_analysis ON events(analysis_key, event_id);
CREATE INDEX IF NOT EXISTS samples_event ON model_samples(event_id);
'''


class FeedbackStore:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else feedback_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'feedback.sqlite3'
        with self.connect() as con:
            con.executescript(SCHEMA)
            if con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] != 1:
                raise ValueError('Unsupported feedback database version. Preserve this database before upgrading.')
            # Additive migration keeps existing corrections, models and legacy
            # Undo chains intact. Older code ignores the optional audit column.
            if 'selection_context' not in {row['name'] for row in con.execute('PRAGMA table_info(events)')}:
                con.execute('ALTER TABLE events ADD COLUMN selection_context TEXT')

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            con.execute('PRAGMA foreign_keys=ON')
            con.execute('PRAGMA journal_mode=WAL')
            with con:
                yield con
        finally:
            con.close()

    @staticmethod
    def bump(con):
        con.execute("UPDATE meta SET value=value+1 WHERE key='revision'")

    @property
    def revision(self):
        with self.connect() as con:
            return con.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]

    def has_analysis(self, key):
        with self.connect() as con:
            return con.execute('SELECT 1 FROM analyses WHERE analysis_key=?', (key,)).fetchone() is not None

    def register(self, key, image_key, name, source_sha, image, parameters, objects):
        """Registration, including all masks, is atomic; repeated analyses keep history."""
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if not con.execute('SELECT 1 FROM images WHERE image_key=?', (image_key,)).fetchone():
                buffer = BytesIO()
                np.save(buffer, image, allow_pickle=False)
                con.execute('INSERT INTO images VALUES (?,?,?,NULL,?)',
                            (image_key, name, zlib.compress(buffer.getvalue()), self.now()))
            con.execute('INSERT OR IGNORE INTO image_sources VALUES (?,?,?)', (image_key, name, source_sha))
            if con.execute('SELECT 1 FROM analyses WHERE analysis_key=?', (key,)).fetchone():
                return
            con.execute('INSERT INTO analyses VALUES (?,?,?,?)', (key, image_key, dumps(parameters), self.now()))
            for obj in objects:
                self.insert_object(con, key, obj)
            self.bump(con)

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def insert_object(con, analysis_key, obj):
        con.execute('''INSERT INTO objects
            (object_key,mask_key,analysis_key,fiber_id,kind,original_classification,original_model_id,
             bbox,mask,features,measurement,manual_classification,active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)''',
                    (obj['object_key'], obj['mask_key'], analysis_key, obj['fiber_id'], obj['kind'],
                     obj['original_classification'], obj.get('original_model_id'), dumps(obj['bbox']),
                     pack_mask(obj['mask']), dumps(obj['features']), dumps(obj['measurement']),
                     obj.get('manual_classification')))

    def objects(self, analysis_key):
        with self.connect() as con:
            rows = con.execute('''SELECT o.*,
                EXISTS(SELECT 1 FROM model_samples s WHERE s.event_id=o.state_event AND s.role='training') AS used_for_training,
                EXISTS(SELECT 1 FROM model_samples s WHERE s.event_id=o.state_event AND s.role='evaluation') AS used_for_evaluation
                FROM objects o WHERE analysis_key=? ORDER BY fiber_id''', (analysis_key,)).fetchall()
        result = []
        for row in rows:
            obj = dict(row)
            for field in ('bbox', 'features', 'measurement'):
                obj[field] = json.loads(obj[field])
            result.append(obj)
        return result

    def image_role(self, image_key):
        with self.connect() as con:
            row = con.execute('SELECT role FROM images WHERE image_key=?', (image_key,)).fetchone()
            return row['role'] if row else None

    @staticmethod
    def assign_role(con, analysis_key, role):
        if role not in ('training', 'evaluation'):
            raise ValueError('Choose training or evaluation for this image.')
        row = con.execute('''SELECT i.image_key,i.role FROM images i JOIN analyses a
            ON i.image_key=a.image_key WHERE a.analysis_key=?''', (analysis_key,)).fetchone()
        if not row:
            raise ValueError('Unknown analysis.')
        if row['role'] is not None and row['role'] != role:
            raise ValueError('This image already has a locked dataset role; changing it would risk evaluation leakage.')
        con.execute('UPDATE images SET role=? WHERE image_key=?', (role, row['image_key']))

    @staticmethod
    def event(con, analysis_key, obj, action, label, active, automatic, model_id, token, undo_target=None,
              selection_context=None):
        cursor = con.execute('''INSERT INTO events
            (analysis_key,object_key,action,before_classification,after_classification,before_active,
             after_active,automatic_classification,model_id,undo_target,event_token,created_at,selection_context)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                             (analysis_key, obj['object_key'], action, obj['manual_classification'], label,
                              obj['active'], active, automatic, model_id, undo_target, token, FeedbackStore.now(),
                              dumps(selection_context) if selection_context is not None else None))
        con.execute('UPDATE objects SET manual_classification=?,active=?,state_event=? WHERE object_key=?',
                    (label, active, cursor.lastrowid, obj['object_key']))
        return cursor.lastrowid

    def correct(self, analysis_key, fiber_id, label, role, automatic, model_id=None, token=None,
                expected_revision=None, selection_context=None):
        if label not in ('accepted', 'rejected'):
            raise ValueError('Classification must be accepted or rejected.')
        token = token or uuid.uuid4().hex
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute('SELECT 1 FROM events WHERE event_token=?', (token,)).fetchone():
                return False
            self.check_revision(con, expected_revision)
            obj = con.execute('SELECT * FROM objects WHERE analysis_key=? AND fiber_id=? AND active=1',
                              (analysis_key, int(fiber_id))).fetchone()
            if not obj:
                raise ValueError('Select an active object in this analysis.')
            self.assign_role(con, analysis_key, role)
            self.event(con, analysis_key, obj, 'classification', label, 1, automatic, model_id, token,
                       selection_context=selection_context)
            self.bump(con)
        return True

    @staticmethod
    def check_revision(con, expected):
        if expected is not None and con.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0] != expected:
            raise ValueError('The review changed. Try again on the refreshed image.')

    def add(self, analysis_key, obj, role, token=None, expected_revision=None, selection_context=None):
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if token and con.execute('SELECT 1 FROM events WHERE event_token=?', (token,)).fetchone():
                return False
            self.check_revision(con, expected_revision)
            self.assign_role(con, analysis_key, role)
            obj = {**obj, 'fiber_id': con.execute('SELECT COALESCE(MAX(fiber_id),0)+1 FROM objects WHERE analysis_key=?',
                                               (analysis_key,)).fetchone()[0], 'manual_classification': None}
            self.insert_object(con, analysis_key, obj)
            before = {**obj, 'active': 0}
            self.event(con, analysis_key, before, 'outline', 'accepted', 1, 'not_detected', None,
                       token or uuid.uuid4().hex, selection_context=selection_context)
            self.bump(con)
        return obj['fiber_id']

    @staticmethod
    def history_stacks(con, analysis_key):
        """Replay audit links, including legacy Undo records, into two stacks.

        Each Undo/Redo links to the current head of its action chain, so the
        existing unique undo_target constraint remains valid on repeated cycles.
        A fresh correction clears the redo stack without deleting any history.
        """
        undo, redo = [], []
        for row in con.execute('SELECT * FROM events WHERE analysis_key=? ORDER BY event_id', (analysis_key,)):
            event = dict(row)
            if event['action'] in ('classification', 'outline'):
                undo.append((event, event['event_id']))
                redo.clear()
            elif event['action'] in ('undo', 'redo'):
                source, target = (undo, redo) if event['action'] == 'undo' else (redo, undo)
                if source and source[-1][1] == event['undo_target']:
                    root, _ = source.pop()
                    target.append((root, event['event_id']))
        return undo, redo

    def undo(self, analysis_key, expected_revision=None, token=None):
        return self._history_move(analysis_key, expected_revision, token, 'undo')

    def redo(self, analysis_key, expected_revision=None, token=None):
        return self._history_move(analysis_key, expected_revision, token, 'redo')

    def _history_move(self, analysis_key, expected_revision, token, action):
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if token and con.execute('SELECT 1 FROM events WHERE event_token=?', (token,)).fetchone():
                return False
            self.check_revision(con, expected_revision)
            undo, redo = self.history_stacks(con, analysis_key)
            stack = undo if action == 'undo' else redo
            if not stack:
                return False
            event, head = stack[-1]
            obj = con.execute('SELECT * FROM objects WHERE object_key=?', (event['object_key'],)).fetchone()
            state = 'before' if action == 'undo' else 'after'
            self.event(con, analysis_key, obj, action, event[state + '_classification'], event[state + '_active'],
                       event['automatic_classification'], event['model_id'], token or uuid.uuid4().hex, head,
                       json.loads(event['selection_context']) if event.get('selection_context') else None)
            self.bump(con)
        return True

    def history(self, analysis_key=None):
        with self.connect() as con:
            return [dict(r) for r in con.execute('''SELECT e.*,a.image_key,i.image_name,o.fiber_id,o.kind,
                o.original_classification,o.original_model_id,o.bbox,o.mask_key,
                EXISTS(SELECT 1 FROM model_samples s WHERE s.event_id=e.event_id AND s.role='training') AS used_for_training,
                EXISTS(SELECT 1 FROM model_samples s WHERE s.event_id=e.event_id AND s.role='evaluation') AS used_for_evaluation
                FROM events e JOIN objects o ON e.object_key=o.object_key
                JOIN analyses a ON e.analysis_key=a.analysis_key JOIN images i ON a.image_key=i.image_key
                WHERE (? IS NULL OR e.analysis_key=?) ORDER BY e.event_id''', (analysis_key, analysis_key))]

    def training_snapshot(self):
        with self.connect() as con:
            con.execute('BEGIN')
            revision = con.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            rows = con.execute('''SELECT o.*,a.image_key,i.role FROM objects o
                JOIN analyses a ON o.analysis_key=a.analysis_key JOIN images i ON a.image_key=i.image_key
                WHERE o.active=1 AND o.manual_classification IS NOT NULL AND i.role IS NOT NULL
                ORDER BY o.state_event DESC''').fetchall()
            references={}
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reference_versions'").fetchone():
                for row in con.execute('''SELECT document_json FROM reference_versions r
                    WHERE sequence=(SELECT MAX(sequence) FROM reference_versions s WHERE s.image_key=r.image_key)'''):
                    doc=json.loads(row[0]);references[doc['image_key']]=doc
            from .grouping import image_group_components
            groups=image_group_components(references.values())
            group_registry=[{k:d.get(k) for k in ('image_key','specimen_id','acquisition_group')}
                            for d in references.values() if d.get('specimen_id') and d.get('acquisition_group')]
        # Same pixels/mask across parameter sets or filenames count as one example.
        seen, samples = set(), []
        for row in rows:
            if row['mask_key'] in seen:
                continue
            seen.add(row['mask_key'])
            m = json.loads(row['measurement'])
            reference=references.get(row['image_key'],{})
            # Operational review of a final-test image must not turn it into a
            # fitting or model-selection example, regardless of a legacy role.
            if reference.get('split')=='test':continue
            samples.append(dict(object_key=row['object_key'], event_id=row['state_event'],
                                image_key=row['image_key'], role=row['role'], label=row['manual_classification'],
                                features=json.loads(row['features']),
                                baseline='accepted' if m['review_status'] == 'accepted' else 'rejected',
                                specimen_id=reference.get('specimen_id'),acquisition_group=reference.get('acquisition_group'),
                                split_group=groups.get(row['image_key']),scientific_split=reference.get('split','unassigned'),
                                reference_version_id=reference.get('version_id'),group_registry=group_registry))
        return revision, samples

    def active_model(self):
        with self.connect() as con:
            row = con.execute('SELECT * FROM models WHERE active=1').fetchone()
        return self.decode_model(row) if row else None

    @staticmethod
    def decode_model(row):
        return {**dict(row), 'payload': json.loads(row['payload']), 'report': json.loads(row['report'])}

    def models(self):
        with self.connect() as con:
            return [self.decode_model(r) for r in con.execute('SELECT * FROM models ORDER BY created_at DESC')]

    def save_model(self, payload, report, samples, expected_revision, approved):
        model_id = uuid.uuid4().hex
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            actual = con.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            if actual != expected_revision:
                approved = False
                report = {**report, 'promoted': False, 'reason': 'Corrections changed during training; retrain on a fresh snapshot.'}
            con.execute('INSERT INTO models VALUES (?,?,?,?,?,0)',
                        (model_id, self.now(), dumps(payload), dumps(report), int(approved)))
            con.executemany('INSERT INTO model_samples VALUES (?,?,?,?,?,?,?)',
                            [(model_id, s['object_key'], s['event_id'], s['image_key'], s['label'],
                              s['role'], dumps(s['features'])) for s in samples])
            if approved:
                con.execute('UPDATE models SET active=0')
                con.execute('UPDATE models SET active=1 WHERE model_id=?', (model_id,))
            self.bump(con)
        return model_id, report

    def activate(self, model_id=None):
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if model_id is not None and not con.execute('SELECT 1 FROM models WHERE model_id=? AND approved=1', (model_id,)).fetchone():
                raise ValueError('Only a previously approved model can be restored.')
            con.execute('UPDATE models SET active=0')
            if model_id:
                con.execute('UPDATE models SET active=1 WHERE model_id=?', (model_id,))
            self.bump(con)

    def backup(self):
        with tempfile.TemporaryDirectory(prefix='collagen-feedback-backup-') as folder:
            path = Path(folder) / 'feedback.sqlite3'
            # sqlite3's transaction context does not close its connection.
            # Close explicitly before TemporaryDirectory cleanup on Windows.
            with self.connect() as source, closing(sqlite3.connect(path)) as target:
                source.backup(target)
            return path.read_bytes()
