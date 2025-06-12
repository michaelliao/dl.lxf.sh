import os
import json
import base64
import hashlib
import boto3

class Dict(dict):
    def __init__(self, **kw):
        super().__init__(**kw)

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"'Dict' object has no attribute '%s'" % key)

    def __setattr__(self, key, value):
        self[key] = value

class Dir(object):
    def __init__(self, part, parent):
        self.part = part
        self.parent = parent
        self.subdirs = []
        self.file_objs = []

    def print_files(self, level=0):
        parent = self.parent.part if self.parent else "N/A"
        print(f'{"  " * level}"{self.part}" parent = {parent}')
        for f in self.file_objs:
            print(f'{"  " * level}  "{f.file}" size = {f.size}')
        for d in self.subdirs:
            d.print_files(level+1)

    def key(self):
        if self.parent is None:
            return ''
        pk = self.parent.key()
        if pk:
            return pk + '/' + self.part
        return self.part

    def to_index(self):
        return Dict(
            key=self.key(),
            files=self.file_objs,
            dirs=[d.part for d in self.subdirs]
        )

def gen_index(index_objs, dir_obj):
    index_objs.append(dir_obj.to_index())
    for d in dir_obj.subdirs:
        gen_index(index_objs, d)

def load_env(key, default_value=None):
    v = os.getenv(key)
    if not v:
        if default_value:
            return default_value
        raise ValueError(f'cannot read env "{key}".')
    return v

def init_boto3():
    endpoint = load_env('ENDPOINT')
    access_key_id = load_env('ACCESS_KEY_ID')
    access_key_secret = load_env('ACCESS_KEY_SECRET')
    region = load_env('REGION', 'auto')
    return boto3.client(
        service_name = 's3',
        endpoint_url = endpoint,
        aws_access_key_id = access_key_id,
        aws_secret_access_key = access_key_secret,
        region_name = region)

def list_objects(s3, bucket):
    print(f'list objects for bucket "{bucket}"...')
    is_truncated = True
    objs = []
    cToken = None
    while is_truncated:
        kw = dict(Bucket=bucket, MaxKeys=1000)
        if cToken:
            kw['ContinuationToken'] = cToken
        response = s3.list_objects_v2(**kw)
        if 'Contents' in response:
            for obj in response['Contents']:
                key = obj['Key']
                objs.append(Dict(
                    key = key,
                    file = os.path.basename(key),
                    size = obj['Size'],
                    last_modified = int(obj['LastModified'].timestamp())
                ))
        is_truncated = response['IsTruncated']
        cToken = response.get('NextContinuationToken', None)
    sorted(objs, key=lambda obj:obj.key)
    return objs

def build_tree(file_objs):
    root = Dir('', None)
    for file_obj in file_objs:
        parts = file_obj.key.split('/')
        current_dir = root
        for part in parts[:-1]:
            found = False
            for d in current_dir.subdirs:
                if d.part == part:
                    current_dir = d
                    found = True
                    break
            if not found:
                new_dir = Dir(part, current_dir)
                current_dir.subdirs.append(new_dir)
                current_dir = new_dir
        current_dir.file_objs.append(file_obj)
    return root

def main():
    bucket = load_env('BUCKET')
    s3 = init_boto3()

    print(f'list files for bucket "{bucket}"...')
    objs = list_objects(s3, bucket)

    # remove exist index.html:
    exist_index_objs = [obj for obj in objs if obj.file == 'index.html']
    # non-index files:
    file_objs = [obj for obj in objs if obj.file != 'index.html']

    root = build_tree(file_objs)
    root.print_files()

    index_files = []
    gen_index(index_files, root)

    with open('index.html', 'r', encoding='utf-8') as fp:
        index_templ = fp.read()

    # upload index files:
    current_index_keys = set()
    for index_file in index_files:
        key = index_file.key + '/index.html'
        if key.startswith('/'):
            key = key[1:]
        current_index_keys.add(key)
        print('upload: ' + key)
        content = index_templ.replace('DIRECTORY_INDEX', json.dumps(index_file, ensure_ascii=False))
        body = content.encode('utf-8')
        md5 = hashlib.md5()
        md5.update(body)
        b64md5 = base64.b64encode(md5.digest()).decode("utf-8")
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType='text/html', ContentMD5=b64md5)

    # remove unused old index files:
    exist_index_keys = [obj.key for obj in exist_index_objs]
    for exist_key in exist_index_keys:
        if not exist_key in current_index_keys:
            print('remove unused index: ' + exist_key)
            s3.delete_object(Bucket=bucket, Key=exist_key)

    print('done.')

if __name__ == '__main__':
    main()
