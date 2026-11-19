"""Versioned human references, separate from predictions and operational review.

An accepted/rejected click is not independent boundary ground truth. Copied
prediction masks are explicitly marked and cannot establish segmentation accuracy.
"""
import base64
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid
import zlib

import numpy as np

from .feedback import feedback_directory, fingerprint, image_identity

SCHEMA_VERSION = 1
CATEGORIES = ('valid_measurable_fiber', 'real_fiber_not_measurable', 'non_fiber', 'ambiguous')
SPLITS = ('unassigned', 'development', 'validation', 'test')
BOUNDARIES = ('independent_manual', 'prediction_reference')


def new_reference(image, image_id, calibration=None):
    return dict(schema_version=SCHEMA_VERSION, version_id=None, parent_version_id=None,
                annotation_revision=0, image_id=image_id, image_key=image_identity(image),
                image_shape=list(image.shape), image_dtype=image.dtype.str,
                calibration_um_per_px=calibration, specimen_id=None, acquisition_group=None,
                magnification=None,calibration_source='',previously_used_for_development=True,
                split='unassigned', reviewer='', coverage='partial', reviewed_regions_xyxy=[], objects=[])


def encode_reference_mask(mask):
    mask=np.asarray(mask)
    if mask.ndim!=2 or mask.dtype!=bool or not mask.any():
        raise ValueError('A reference mask must be a nonempty two-dimensional boolean array.')
    yy,xx=np.nonzero(mask)
    y0,x0,y1,x1=int(yy.min()),int(xx.min()),int(yy.max()+1),int(xx.max()+1)
    packed=np.packbits(mask[y0:y1,x0:x1].ravel()).tobytes()
    return dict(bbox_yxyx=[y0,x0,y1,x1], mask_encoding='zlib-packbits-big-base64',
                mask_data=base64.b64encode(zlib.compress(packed)).decode('ascii'))


def decode_reference_mask(obj, shape):
    bbox=obj.get('bbox_yxyx')
    if (not isinstance(bbox,list) or len(bbox)!=4
            or any(isinstance(v,bool) or not isinstance(v,int) for v in bbox)):
        raise ValueError('Reference bounding boxes need four original integer pixel coordinates.')
    y0,x0,y1,x1=bbox
    if not (0<=y0<y1<=shape[0] and 0<=x0<x1<=shape[1]):
        raise ValueError('Reference mask is outside the original image.')
    if obj.get('mask_encoding')!='zlib-packbits-big-base64':
        raise ValueError('Unsupported reference mask encoding.')
    count=(y1-y0)*(x1-x0);size=(count+7)//8
    try:
        compressed=base64.b64decode(obj['mask_data'],validate=True)
        decoder=zlib.decompressobj()
        raw=decoder.decompress(compressed,size+1)
        if len(raw)!=size or not decoder.eof or decoder.unused_data:
            raise ValueError('Incorrect reference mask size.')
        mask=np.unpackbits(np.frombuffer(raw,np.uint8),count=count).reshape(y1-y0,x1-x0).astype(bool)
    except (KeyError,TypeError,ValueError,zlib.error) as error:
        raise ValueError('Invalid reference mask data.') from error
    if not mask.any():
        raise ValueError('Reference masks must contain foreground pixels.')
    return mask,tuple(bbox)


def reference_object(mask, category, reviewer, boundary_source='independent_manual',
                     object_id=None, length_um=None, mean_diameter_um=None,
                     measurement_source='', original_prediction=None, note='', offset_yx=(0,0)):
    encoded=encode_reference_mask(mask)
    y0,x0,y1,x1=encoded['bbox_yxyx'];dy,dx=offset_yx
    encoded['bbox_yxyx']=[y0+dy,x0+dx,y1+dy,x1+dx]
    return dict(object_id=object_id or uuid.uuid4().hex, category=category, reviewer=reviewer,
                boundary_source=boundary_source, length_um=length_um, mean_diameter_um=mean_diameter_um,
                measurement_source=measurement_source, original_prediction=deepcopy(original_prediction),
                note=note, **encoded)


def validate_reference(document, image=None):
    """Validate types, units, identity and geometry; never infer reference labels."""
    if document.get('schema_version')!=SCHEMA_VERSION:
        raise ValueError('Unsupported reference schema version.')
    revision=document.get('annotation_revision')
    if isinstance(revision,bool) or not isinstance(revision,int) or revision<0:
        raise ValueError('Reference annotation_revision must be a nonnegative integer.')
    for field in ('version_id','parent_version_id'):
        value=document.get(field)
        if value is not None and (not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value)):
            raise ValueError('Reference version IDs must be SHA-256 keys or null.')
    shape=document.get('image_shape')
    if (not isinstance(shape,list) or len(shape) not in (2,3)
            or any(isinstance(v,bool) or not isinstance(v,int) or v<=0 for v in shape)
            or (len(shape)==3 and shape[2] not in (3,4))):
        raise ValueError('Reference image_shape must describe the original single-plane image.')
    key=document.get('image_key')
    if not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('Reference image identity must be a decoded-pixel SHA-256 key.')
    if not isinstance(document.get('image_id'),str) or not document['image_id'].strip():
        raise ValueError('Reference image_id is required.')
    try:
        np.dtype(document['image_dtype'])
    except (KeyError,TypeError) as error:
        raise ValueError('Reference image_dtype is required.') from error
    if image is not None and (list(image.shape)!=shape or image.dtype.str!=document['image_dtype']
                              or image_identity(image)!=key):
        raise ValueError('Reference pixels, shape or dtype do not match this original image.')
    if document.get('split') not in SPLITS:
        raise ValueError('Reference split must be unassigned, development, validation or test.')
    if not isinstance(document.get('previously_used_for_development',True),bool):
        raise ValueError('Development-exposure metadata must be explicitly true or false.')
    for field in ('specimen_id','acquisition_group'):
        value=document.get(field)
        if value is not None and (not isinstance(value,str) or not value.strip()):
            raise ValueError(f'{field} must be a nonempty identifier or null until verified.')
    calibration=document.get('calibration_um_per_px')
    if calibration is not None and (isinstance(calibration,bool) or not isinstance(calibration,(int,float))
                                   or not np.isfinite(calibration) or calibration<=0):
        raise ValueError('Reference calibration must be verified positive µm/pixel, or null.')
    if document.get('coverage') not in ('partial','exhaustive_regions'):
        raise ValueError('Reference coverage must be partial or exhaustive_regions.')
    regions=document.get('reviewed_regions_xyxy')
    if not isinstance(regions,list):
        raise ValueError('Reviewed regions must be a list of original-pixel rectangles.')
    for r in regions:
        if (not isinstance(r,list) or len(r)!=4 or any(isinstance(v,bool) or not isinstance(v,int) for v in r)
                or not (0<=r[0]<r[2]<=shape[1] and 0<=r[1]<r[3]<=shape[0])):
            raise ValueError('Reviewed rectangles must be inside the original image.')
    if document['coverage']=='exhaustive_regions' and not regions:
        raise ValueError('Exhaustive reference coverage requires explicitly reviewed regions.')
    if document['coverage']=='exhaustive_regions' and not str(document.get('reviewer','')).strip():
        raise ValueError('Record who confirmed exhaustive reference-region coverage.')
    objects=document.get('objects')
    if not isinstance(objects,list):
        raise ValueError('Reference objects must be a list.')
    ids=set()
    for obj in objects:
        oid=obj.get('object_id')
        if not isinstance(oid,str) or not oid or oid in ids:
            raise ValueError('Reference object IDs must be present and unique within the image.')
        ids.add(oid)
        if obj.get('category') not in CATEGORIES or obj.get('boundary_source') not in BOUNDARIES:
            raise ValueError('Unknown reference category or boundary provenance.')
        if not isinstance(obj.get('reviewer'),str) or not obj['reviewer'].strip():
            raise ValueError('Record who independently reviewed each reference.')
        decode_reference_mask(obj,shape)
        for metric in ('length_um','mean_diameter_um'):
            value=obj.get(metric)
            if value is not None:
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not np.isfinite(value) or value<=0:
                    raise ValueError('Manual measurements must be positive finite µm values or null.')
                if not str(obj.get('measurement_source','')).strip():
                    raise ValueError('Record the independent source of manual measurements.')
    # Reject non-finite/unsupported JSON even in ancillary provenance fields.
    json.dumps(document,allow_nan=False)
    return document


def upsert_reference(document, obj, image=None):
    updated=deepcopy(document)
    previous=next((i for i,o in enumerate(updated['objects']) if o['object_id']==obj['object_id']),None)
    if previous is None:updated['objects'].append(deepcopy(obj))
    else:updated['objects'][previous]=deepcopy(obj)
    validate_reference(updated,image)
    return updated


def split_groups(documents):
    """Transitive specimen OR acquisition connections must remain together."""
    documents=list(documents)
    parents=list(range(len(documents)))
    def root(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]];i=parents[i]
        return i
    seen={}
    image_seen={}
    for i,d in enumerate(documents):
        validate_reference(d)
        for field in ('specimen_id','acquisition_group'):
            if not d.get(field):
                raise ValueError('Verified specimen_id and acquisition_group are required for grouped evaluation.')
            key=(field,d[field])
            if key in seen:parents[root(i)]=root(seen[key])
            else:seen[key]=i
        if d['image_key'] in image_seen:
            raise ValueError('Duplicate source pixels cannot be separate benchmark images or split aliases.')
        image_seen[d['image_key']]=i
    groups={}
    for i,d in enumerate(documents):groups.setdefault(root(i),[]).append(d)
    for group in groups.values():
        assigned={d['split'] for d in group if d['split']!='unassigned'}
        if len(assigned)>1:
            raise ValueError('Specimen/acquisition leakage: connected images occupy different dataset splits.')
        if 'test' in assigned and any(d.get('previously_used_for_development',True) for d in group):
            raise ValueError('Previously inspected/tuned specimen groups cannot be relabeled as an independent final test.')
    return list(groups.values())


def assign_group_splits(documents, seed=42):
    """Create a reproducible proposed split, requiring at least three groups."""
    docs=deepcopy(list(documents));groups=split_groups(docs)
    if any(d['split']!='unassigned' for d in docs):
        raise ValueError('Split assignment requires an unassigned manifest; do not silently change a held-out split.')
    if len(groups)<3:raise ValueError('At least three independent specimen/acquisition groups are needed for three splits.')
    groups.sort(key=lambda group:sorted(d['image_key'] for d in group))
    order=list(np.random.default_rng(seed).permutation(len(groups)))
    fresh=[i for i in order if not any(d.get('previously_used_for_development',True) for d in groups[i])]
    if not fresh:raise ValueError('An independent final test requires a specimen group not previously used for development.')
    test=set(fresh[:max(1,min(len(fresh),len(groups)//5))])
    remaining=[i for i in order if i not in test]
    validation=set(remaining[:max(1,len(groups)//5)])
    for index in order:
        role='test' if index in test else 'validation' if index in validation else 'development'
        for d in groups[index]:d['split']=role
    split_groups(docs)
    return docs


class ReferenceStore:
    """Immutable annotation snapshots in the existing backed-up SQLite database."""
    def __init__(self, directory=None):
        self.directory=Path(directory) if directory is not None else feedback_directory()
        self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'feedback.sqlite3'
        with closing(sqlite3.connect(self.path,timeout=30)) as con, con:
            con.execute('''CREATE TABLE IF NOT EXISTS reference_versions (
                sequence INTEGER PRIMARY KEY, image_key TEXT NOT NULL, version_id TEXT NOT NULL UNIQUE,
                parent_version_id TEXT, created_at TEXT NOT NULL, document_json TEXT NOT NULL)''')
            con.execute('CREATE INDEX IF NOT EXISTS reference_images ON reference_versions(image_key,sequence)')

    def load(self, image_key):
        with closing(sqlite3.connect(self.path,timeout=30)) as con, con:
            row=con.execute('SELECT document_json FROM reference_versions WHERE image_key=? ORDER BY sequence DESC LIMIT 1',
                            (image_key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, document, image=None):
        validate_reference(document,image)
        doc=deepcopy(document)
        with closing(sqlite3.connect(self.path,timeout=30)) as con, con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT version_id FROM reference_versions WHERE image_key=? ORDER BY sequence DESC LIMIT 1',
                            (doc['image_key'],)).fetchone()
            parent=row[0] if row else None
            if doc.get('version_id')!=parent:
                raise ValueError('Reference annotations changed. Reload before saving another version.')
            doc['parent_version_id']=parent;doc['annotation_revision']+=1
            doc['created_at']=datetime.now(timezone.utc).isoformat()
            doc['version_id']=fingerprint(parent,doc,uuid.uuid4().hex)
            con.execute('INSERT INTO reference_versions(image_key,version_id,parent_version_id,created_at,document_json) VALUES(?,?,?,?,?)',
                        (doc['image_key'],doc['version_id'],parent,doc['created_at'],json.dumps(doc,allow_nan=False)))
            # Training snapshots include reference group metadata. Invalidate
            # in-flight promotion if it changes, without saving any binary label.
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
                con.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
        return doc

    def versions(self, image_key):
        with closing(sqlite3.connect(self.path,timeout=30)) as con, con:
            return [json.loads(r[0]) for r in con.execute('SELECT document_json FROM reference_versions WHERE image_key=? ORDER BY sequence',(image_key,))]

    def version_headers(self, image_key):
        with closing(sqlite3.connect(self.path,timeout=30)) as con, con:
            return [dict(version_id=r[0],parent_version_id=r[1],created_at=r[2]) for r in con.execute(
                'SELECT version_id,parent_version_id,created_at FROM reference_versions WHERE image_key=? ORDER BY sequence',(image_key,))]
