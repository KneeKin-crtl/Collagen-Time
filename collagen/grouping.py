"""Transitive specimen/acquisition identity, independent of crops and filenames."""
import hashlib
import json


def image_group_components(records):
    records=list(records);parents={r['image_key']:r['image_key'] for r in records}
    def root(key):
        while parents[key]!=key:
            parents[key]=parents[parents[key]];key=parents[key]
        return key
    seen={}
    for r in records:
        for field in ('specimen_id','acquisition_group'):
            if not r.get(field):continue
            key=(field,r[field])
            if key in seen:parents[root(r['image_key'])]=root(seen[key])
            else:seen[key]=r['image_key']
    members={}
    for key in parents:members.setdefault(root(key),[]).append(key)
    hashes={parent:hashlib.sha256(json.dumps(sorted(keys)).encode()).hexdigest() for parent,keys in members.items()}
    return {key:hashes[root(key)] for key in parents}
